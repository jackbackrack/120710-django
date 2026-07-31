from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.functional import cached_property


def artist_directory_emails():
    """Addresses that belong to an artist the gallery already has on file.

    A queryset rather than a list, so callers use it as a subquery and it stays one round
    trip. Lower-cased to match `Subscriber.email`, which is normalised on save; artist
    records are typed by hand and are not.
    """
    from django.db.models.functions import Lower

    from gallery.models.people import Artist
    return (Artist.objects.exclude(email='')
            .annotate(lowered=Lower('email')).values_list('lowered', flat=True))


def segment_q(segment, prefix=''):
    """A filter for one segment, against Subscriber or anything related to it.

    `prefix` is the path to the subscriber — '' when filtering subscribers, 'subscriber'
    when filtering subscriptions — so a campaign and the staff list share this rather than
    writing the same disjunction twice and drifting.
    """
    p = f'{prefix}__' if prefix else ''
    declared = {
        Subscriber.COLLECTOR: Q(**{f'{p}is_collector': True}),
        Subscriber.FUNDER: Q(**{f'{p}is_funder': True}),
    }
    if segment in declared:
        return declared[segment]
    # Artist is the one segment with a second source: somebody in the artist directory is an
    # artist whether or not they ever ticked a box on the subscribe form.
    in_directory = Q(**{f'{p}email__in': artist_directory_emails()})
    if segment == Subscriber.ARTIST:
        return Q(**{f'{p}is_artist': True}) | in_directory
    if segment == Subscriber.VISITOR:
        return (Q(**{f'{p}is_artist': False})
                & Q(**{f'{p}is_collector': False})
                & Q(**{f'{p}is_funder': False})
                & ~in_directory)
    return Q()


class Subscriber(models.Model):
    """One person, identified by their email address.

    A person, not a list membership — that is `Subscription`. The split exists because a
    network of galleries means someone can follow 120710, or reset.art, or both, and the
    first shape of this model made that two rows, with two names and two independent
    unsubscribe states. "Stop emailing me" then had nowhere single to be recorded, which is
    both a poor experience and the wrong answer to a GDPR erasure request.

    The list lives here rather than at the email provider. Resend's marketing product prices
    by contact count and would bill twice for a person on two galleries' lists; here they are
    one row and cost nothing.
    """

    # ── Segments ─────────────────────────────────────────────────────────────
    #
    # What somebody is here for, so a mailing can go to the people it is actually about: an
    # open call to artists, a preview to collectors, a report to funders.
    #
    # Several at once, deliberately. In a small scene the same person paints, buys and sits
    # on a board, and forcing one label would mean choosing which of those to stop mailing
    # them about.
    #
    # **Visitor is not stored.** It is what is left when nobody has said anything else, which
    # keeps it true by construction: a stored flag would be a fourth thing to keep in sync,
    # and somebody ticking both "collector" and "visitor" would be a state with no meaning.
    ARTIST = 'artist'
    COLLECTOR = 'collector'
    FUNDER = 'funder'
    VISITOR = 'visitor'
    INTEREST_CHOICES = [
        (ARTIST, 'Artist'),
        (COLLECTOR, 'Collector'),
        (FUNDER, 'Funder'),
    ]
    SEGMENT_CHOICES = INTEREST_CHOICES + [(VISITOR, 'Visitor')]

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=255, blank=True, default='')
    last_name = models.CharField(max_length=255, blank=True, default='')
    is_artist = models.BooleanField(default=False)
    is_collector = models.BooleanField(default=False)
    is_funder = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['email']
        indexes = [models.Index(fields=['is_artist', 'is_collector', 'is_funder'])]

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        return ' '.join(filter(None, [self.first_name, self.last_name]))

    @cached_property
    def in_artist_directory(self):
        """Whether this address belongs to an artist the gallery has on file.

        Derived rather than copied onto the row, so it stays true as artists are added: an
        artist who joined the list before they had a profile becomes one the moment they do,
        with nothing to re-run.

        A `cached_property` so a page listing subscribers can annotate the same name on the
        queryset and shadow it — one query for the page instead of one per person.
        """
        from gallery.models.people import Artist
        return Artist.objects.filter(email__iexact=self.email).exists()

    @property
    def segments(self):
        """Every segment this person is in. Visitor when none of the others apply."""
        found = []
        if self.is_artist or self.in_artist_directory:
            found.append(self.ARTIST)
        if self.is_collector:
            found.append(self.COLLECTOR)
        if self.is_funder:
            found.append(self.FUNDER)
        return found or [self.VISITOR]

    @property
    def segment_labels(self):
        labels = dict(self.SEGMENT_CHOICES)
        return [labels[name] for name in self.segments]

    def set_interests(self, interests, *, additive=True):
        """Record what somebody says they are. Returns the field names that changed.

        Additive by default: a subscribe form arrives with nothing ticked far more often
        than it arrives meaning "forget what I told you last time", so re-subscribing must
        not quietly wipe what is already known. The staff page passes additive=False, since
        an operator unticking a box plainly means to remove it.
        """
        wanted = set(interests or ())
        changed = []
        for name, field in ((self.ARTIST, 'is_artist'), (self.COLLECTOR, 'is_collector'),
                            (self.FUNDER, 'is_funder')):
            new = getattr(self, field) or name in wanted if additive else name in wanted
            if new != getattr(self, field):
                setattr(self, field, new)
                changed.append(field)
        return changed

    def save(self, *args, **kwargs):
        # Lower-cased so uniqueness is case-insensitive without a citext column or an index
        # only Postgres would honour.
        self.email = (self.email or '').strip().lower()
        super().save(*args, **kwargs)

    # ── The one way in ───────────────────────────────────────────────────────

    @staticmethod
    def default_site():
        """Whose list an unscoped opt-in joins.

        The deployment's own venue, matching `default_site` in the context processor. None
        is the network-wide list — reset.art's own — which is also what a deployment with no
        default venue has.
        """
        from django.conf import settings

        from gallery.models.sites import Site

        slug = getattr(settings, 'GALLERY_DEFAULT_SITE_SLUG', None)
        if not slug:
            return None
        return Site.objects.filter(slug=slug, status=Site.STATUS_PUBLISHED).first()

    @classmethod
    def opt_in(cls, *, email, sites=None, first_name='', last_name='', source=None,
               interests=None):
        """Subscribe one person to one or more lists. Returns (subscriber, subscriptions).

        `sites` is a list which may contain None, meaning the network-wide list; omitting it
        means the deployment's default. Idempotent, and it re-subscribes someone who
        previously opted out — they asked, and refusing would be worse than honouring it.

        A name supplied now overwrites a blank or worse one from an import: a person typing
        their own name into a form is the best source available. Interests only ever add,
        for the reason in `set_interests`.
        """
        email = (email or '').strip().lower()
        subscriber, created = cls.objects.get_or_create(
            email=email,
            defaults={'first_name': first_name, 'last_name': last_name})
        changed = []
        if first_name and first_name != subscriber.first_name:
            subscriber.first_name = first_name
            changed.append('first_name')
        if last_name and last_name != subscriber.last_name:
            subscriber.last_name = last_name
            changed.append('last_name')
        changed += subscriber.set_interests(interests)
        if changed and not created:
            subscriber.save(update_fields=changed + ['updated_at'])
        elif changed:
            subscriber.save(update_fields=changed)

        chosen = sites if sites is not None else [cls.default_site()]
        subscriptions = [Subscription.subscribe(subscriber, site, source=source)
                         for site in chosen]
        return subscriber, subscriptions

    def unsubscribe_all(self, reason=None):
        """Off every list. What "stop emailing me" has to be able to mean."""
        count = 0
        for subscription in self.subscriptions.filter(is_subscribed=True):
            if subscription.unsubscribe(reason=reason or Subscription.UNSUB_REQUESTED):
                count += 1
        return count

    @property
    def subscribed_sites(self):
        return [s.site for s in self.subscriptions.filter(is_subscribed=True)]


class Subscription(models.Model):
    """One person's membership of one list.

    A null site is the network-wide list — reset.art's own — so a deployment that has not
    created a Site row still has somewhere for subscribers to go.
    """

    SOURCE_SUBSCRIBE_FORM = 'subscribe_form'
    SOURCE_KIOSK = 'kiosk'
    SOURCE_ARTIST_PROFILE = 'artist_profile'
    SOURCE_IMPORT = 'import'
    SOURCE_MANUAL = 'manual'
    SOURCE_CHOICES = [
        (SOURCE_SUBSCRIBE_FORM, 'Subscribe form'),
        (SOURCE_KIOSK, 'Kiosk form'),
        (SOURCE_ARTIST_PROFILE, 'Artist profile'),
        (SOURCE_IMPORT, 'Imported'),
        (SOURCE_MANUAL, 'Added by hand'),
    ]

    # Both mean stop sending, but a complaint is a person saying so and a bounce is an
    # address that stopped working. Worth telling apart when deciding whether to try again.
    UNSUB_REQUESTED = 'requested'
    UNSUB_BOUNCED = 'bounced'
    UNSUB_COMPLAINED = 'complained'
    UNSUB_REASONS = [
        (UNSUB_REQUESTED, 'Unsubscribed'),
        (UNSUB_BOUNCED, 'Hard bounce'),
        (UNSUB_COMPLAINED, 'Spam complaint'),
    ]

    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE,
                                   related_name='subscriptions')
    site = models.ForeignKey('gallery.Site', null=True, blank=True,
                             on_delete=models.CASCADE, related_name='subscriptions',
                             help_text='Blank for the network-wide list.')
    is_subscribed = models.BooleanField(default=True)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES,
                              default=SOURCE_SUBSCRIBE_FORM)
    unsubscribed_reason = models.CharField(max_length=16, choices=UNSUB_REASONS,
                                           blank=True, default='')
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['site__name', 'subscriber__email']
        constraints = [
            models.UniqueConstraint(fields=['subscriber', 'site'],
                                    name='unique_subscription_per_site'),
            # Postgres treats NULLs as distinct, so the constraint above does not cover the
            # network-wide list. This one does.
            models.UniqueConstraint(fields=['subscriber'], condition=Q(site__isnull=True),
                                    name='unique_network_subscription'),
        ]

    def __str__(self):
        return f'{self.subscriber.email} → {self.list_name}'

    @property
    def list_name(self):
        return self.site.name if self.site_id else 'reset.art'

    @classmethod
    def subscribe(cls, subscriber, site=None, source=None):
        subscription, created = cls.objects.get_or_create(
            subscriber=subscriber, site=site,
            defaults={'source': source or cls.SOURCE_SUBSCRIBE_FORM})
        if not created and not subscription.is_subscribed:
            subscription.is_subscribed = True
            subscription.unsubscribed_reason = ''
            subscription.unsubscribed_at = None
            subscription.save(update_fields=['is_subscribed', 'unsubscribed_reason',
                                             'unsubscribed_at', 'updated_at'])
        return subscription

    def unsubscribe(self, reason=UNSUB_REQUESTED):
        """Idempotent: unsubscribing twice must not move the timestamp."""
        if not self.is_subscribed:
            return False
        self.is_subscribed = False
        self.unsubscribed_reason = reason
        self.unsubscribed_at = timezone.now()
        self.save(update_fields=['is_subscribed', 'unsubscribed_reason',
                                 'unsubscribed_at', 'updated_at'])
        return True
