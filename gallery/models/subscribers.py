from django.db import models
from django.db.models import Q
from django.utils import timezone


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

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=255, blank=True, default='')
    last_name = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['email']

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        return ' '.join(filter(None, [self.first_name, self.last_name]))

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
    def opt_in(cls, *, email, sites=None, first_name='', last_name='', source=None):
        """Subscribe one person to one or more lists. Returns (subscriber, subscriptions).

        `sites` is a list which may contain None, meaning the network-wide list; omitting it
        means the deployment's default. Idempotent, and it re-subscribes someone who
        previously opted out — they asked, and refusing would be worse than honouring it.

        A name supplied now overwrites a blank or worse one from an import: a person typing
        their own name into a form is the best source available.
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
        if changed and not created:
            subscriber.save(update_fields=changed + ['updated_at'])

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
