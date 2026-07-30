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
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SENDING, 'Sending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
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
    def can_send(self):
        return self.status == self.STATUS_DRAFT and self.is_tested and bool(self.subject)

    @property
    def blocked_reason(self):
        """Why the send button is disabled, in words a person can act on."""
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
