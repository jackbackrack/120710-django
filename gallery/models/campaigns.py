from datetime import timedelta

from django.db import models
from django.utils import timezone


class Campaign(models.Model):
    """One mailing to a site's subscribers.

    Two authoring paths, deliberately. Most campaigns are a recurring shape filled from the
    database — an open call announcement, a show opening — and those are Django templates
    rendering MJML, so they reach the ORM and nobody retypes a date. The occasional one-off
    is Markdown in `body_markdown`, which needs no markup knowledge and is the easiest thing
    to draft or revise.

    Both render through the same tested MJML shell, so the header, footer, physical address
    and unsubscribe link are identical however the body was written.
    """

    STATUS_DRAFT = 'draft'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    # Stopped deliberately, part-way, with people still to go. A warm-up sends a few hundred a
    # day to a domain with no sending history, and calling that state "failed" would be a lie
    # told to the one person who needs to know the difference.
    STATUS_PAUSED = 'paused'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SENDING, 'Sending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_PAUSED, 'Paused'),
    ]

    site = models.ForeignKey(
        'gallery.Site', null=True, blank=True, on_delete=models.CASCADE,
        related_name='campaigns',
        help_text='Whose subscribers this goes to. Blank for the network-wide list.')
    subject = models.CharField(max_length=255)
    # Shown after the subject in most inboxes. Left empty, clients scrape the first words of
    # the body instead, which is usually "View this email in your browser".
    preheader = models.CharField(
        max_length=255, blank=True, default='', verbose_name='Preview text',
        help_text='The line inboxes show after the subject. Around 90 characters.')

    body_markdown = models.TextField(
        blank=True, default='', verbose_name='Body (Markdown)',
        help_text='For a one-off. Leave blank if this campaign uses a template.')
    template_name = models.CharField(
        max_length=255, blank=True, default='',
        help_text='For a recurring shape: an MJML template under templates/email/campaigns/, '
                  'e.g. "open_call.mjml". Takes precedence over the Markdown body.')

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # The send guard. `edited_at` moves whenever the content changes; `test_sent_at` moves
    # when a test goes out. Sending is refused unless test_sent_at is the later of the two,
    # which makes "sent the draft with TODO still in it" structurally impossible rather than
    # a matter of remembering.
    edited_at = models.DateTimeField(auto_now_add=True)
    test_sent_at = models.DateTimeField(null=True, blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    recipient_count = models.PositiveIntegerField(default=0)
    # Moved after every batch. A send runs in a background thread, so the web process can
    # be restarted — a deploy, a crash, Railway moving the container — while a send is in
    # flight, and nothing would otherwise mark the campaign as no longer progressing. A
    # stale timestamp against status=sending is how an abandoned send becomes visible.
    progress_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='campaigns')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.subject

    @property
    def is_tested(self):
        """A test has gone out since the last content change."""
        return bool(self.test_sent_at and self.test_sent_at >= self.edited_at)

    @property
    def list_is_sendable(self):
        """Whether the list this campaign targets may be mailed at all yet.

        The network-wide list is reset.art's, and reset.art has no email authentication of its
        own — DKIM keys are per-domain and none of 120710.art's carry over. Sending would work
        in the sense that the messages would leave, which is exactly the problem: they would go
        out branded as a domain that cannot be verified as the sender, on a first impression
        that is hard to take back.
        """
        from django.conf import settings

        if self.site_id:
            return True
        return bool(getattr(settings, 'CAMPAIGN_NETWORK_LIST_ENABLED', False))

    @property
    def can_send(self):
        return (self.status == self.STATUS_DRAFT and self.is_tested
                and bool(self.subject) and self.list_is_sendable)

    # How long a send may go without recording a batch before it is presumed abandoned.
    # Comfortably longer than a batch takes, so a slow provider is not mistaken for a dead
    # worker; short enough that an operator is not left staring at a stuck page.
    STALL_AFTER = timedelta(minutes=10)

    @property
    def is_stalled(self):
        """Sending, but nothing has been recorded for a while.

        The case this catches: the process running the send went away mid-flight. Nothing
        can report that from the inside, so it is inferred from the absence of progress.
        """
        if self.status != self.STATUS_SENDING:
            return False
        since = self.progress_at or self.sent_at or self.edited_at
        return timezone.now() - since > self.STALL_AFTER

    @property
    def can_resume(self):
        """A send that stopped part-way can be picked up where it left off.

        Covers both ways a send stops: it raised (FAILED), or the process vanished and left
        the row saying SENDING forever. The second is the more dangerous one, because it
        looks like work in progress rather than a problem.

        No fresh test is required. The content has not changed since the send that started,
        and demanding one would put a hurdle between a half-mailed list and finishing it.
        """
        if not self.list_is_sendable:
            return False
        return (self.status in (self.STATUS_FAILED, self.STATUS_PAUSED)
                or self.is_stalled)

    @property
    def sent_so_far(self):
        """How many people have actually received this, from the delivery records."""
        return self.deliveries.filter(status='sent').count()

    @property
    def rejected(self):
        """Addresses the provider refused, which are now unsubscribed as hard bounces.

        Surfaced on the campaign page rather than left in a log, because "three addresses were
        rejected" is not something anybody can act on and "mailbox does not exist" is.
        """
        return (self.deliveries.filter(status='rejected')
                .select_related('subscription__subscriber'))

    @property
    def remaining_count(self):
        """How many are still owed this campaign. Import-local to avoid a cycle."""
        from gallery.campaigns import pending
        return pending(self).count()

    @property
    def blocked_reason(self):
        """Why the send button is disabled, in words a person can act on."""
        if not self.list_is_sendable:
            return ('The network-wide (reset.art) list cannot be mailed yet, because reset.art '
                    'does not have its own email authentication set up. Send to a venue\'s own '
                    'list instead, or see docs/reset-art-cutover.md.')
        if self.can_resume:
            what = {self.STATUS_FAILED: 'failed', self.STATUS_PAUSED: 'was paused'}.get(
                self.status, 'stopped')
            return (f'This send {what} after {self.sent_so_far} of '
                    f'{self.sent_so_far + self.remaining_count} message(s). '
                    f'Resume it to send the rest — nobody will get it twice.')
        if self.status != self.STATUS_DRAFT:
            return f'This campaign is already {self.get_status_display().lower()}.'
        if not self.subject:
            return 'It needs a subject.'
        if not self.is_tested:
            if self.test_sent_at:
                return 'It has changed since the last test — send another test first.'
            return 'Send a test to yourself first.'
        return ''

    def touch_edited(self):
        """Called when the content changes, which re-arms the send guard."""
        self.edited_at = timezone.now()

    def save(self, *args, **kwargs):
        # Any change to what a recipient would see invalidates an earlier test.
        if self.pk:
            previous = Campaign.objects.filter(pk=self.pk).values(
                'subject', 'preheader', 'body_markdown', 'template_name').first()
            if previous and any(previous[f] != getattr(self, f) for f in previous):
                self.edited_at = timezone.now()
        super().save(*args, **kwargs)


class CampaignDelivery(models.Model):
    """What happened when this campaign was sent to one person.

    Exists so a send that dies part-way can be resumed. Without it a failure left no record of
    how far it got, so the only options were to abandon the campaign or to re-send it and mail
    the first several hundred people twice.

    It records rejections as well as successes, and that is the point of `status`: an address
    the provider refuses is settled, not pending. Recording it means a resume skips it rather
    than retrying it forever, and the operator can see which addresses they were without
    reading a server log.

    Also useful on its own: it makes "was Ana sent the March mailing?" answerable.
    """

    STATUS_SENT = 'sent'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_SENT, 'Sent'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    campaign = models.ForeignKey('gallery.Campaign', on_delete=models.CASCADE,
                                 related_name='deliveries')
    # CASCADE: erasing a subscriber erases the record of having mailed them, which is
    # what an erasure request means.
    subscription = models.ForeignKey('gallery.Subscription', on_delete=models.CASCADE,
                                     related_name='deliveries')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SENT)
    # The provider's own words, for a rejection. Kept because "3 addresses were rejected" is
    # not actionable and "mailbox does not exist" is.
    error = models.CharField(max_length=255, blank=True, default='')
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Belt and braces against a double-send: even if two sends race, the same
            # person cannot be recorded twice for one campaign.
            models.UniqueConstraint(fields=['campaign', 'subscription'],
                                    name='unique_delivery_per_campaign'),
        ]
        indexes = [models.Index(fields=['campaign', 'subscription'])]

    def __str__(self):
        return f'{self.campaign} → {self.subscription.subscriber.email} ({self.status})'
