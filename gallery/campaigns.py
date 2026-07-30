"""Rendering and sending campaigns.

The pipeline is: Django template (MJML source with {{ }} tags) → rendered with context →
compiled by MJML → HTML that survives Outlook. Rendering MJML *through* Django's template
engine rather than authoring static MJML is the whole point — a campaign about a show can
then say `{% for artwork in show.artworks.all %}` and nobody retypes a date.

Sending goes through Resend's transactional API rather than a Resend audience, so the list
is `Subscriber` and unsubscribe is ours. See gallery/models/subscribers.py for why.
"""
import logging
import re

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, strip_tags
from django.utils.safestring import mark_safe

from gallery.models import Campaign, Subscriber, Subscription

logger = logging.getLogger(__name__)

# Resend takes up to 100 messages per batch call. Each recipient's mail differs — the
# unsubscribe link is per-person — so this is a real batch of distinct messages, not one
# message with many recipients.
BATCH_SIZE = 100

UNSUBSCRIBE_SALT = 'gallery.campaigns.unsubscribe'


# ── Unsubscribe tokens ───────────────────────────────────────────────────────

def unsubscribe_token(subscription):
    """A signed token naming one subscription — a person *and* which list.

    Signed rather than random-and-stored: no extra column, no lookup table, and it cannot be
    guessed or enumerated. Deliberately without an expiry — an unsubscribe link in a
    two-year-old email must still work, or the recipient's only remaining option is the spam
    button, which is the outcome the whole mechanism exists to avoid.

    It names the subscription rather than the person so the landing page can offer the right
    default: leave *this* gallery's list, with leaving all of them one click further.
    """
    return signing.dumps({'pk': subscription.pk,
                          'email': subscription.subscriber.email},
                         salt=UNSUBSCRIBE_SALT)


def subscription_from_token(token):
    """The subscription a token names, or None if it does not verify."""
    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    except signing.BadSignature:
        return None
    subscription = (Subscription.objects
                    .select_related('subscriber', 'site')
                    .filter(pk=data.get('pk')).first())
    # The email is in the token as well as the pk, so a recycled primary key cannot
    # unsubscribe the wrong person.
    if subscription and subscription.subscriber.email == data.get('email'):
        return subscription
    return None


def _absolute(path, request=None):
    """An absolute URL for a link in an email, with or without a request.

    Campaigns are usually sent from a request, but send_campaign can be called from a
    shell or a management command, where build_absolute_uri is not available.
    """
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, 'SITE_BASE_URL', 'https://www.120710.art').rstrip('/')
    return f'{base}{path}'


def unsubscribe_url(subscription, request=None):
    return _absolute(
        reverse('unsubscribe', kwargs={'token': unsubscribe_token(subscription)}), request)


def privacy_url(site=None, request=None):
    """The policy, scoped to the venue whose mail this is when there is one."""
    if site is not None:
        return _absolute(reverse('site_privacy', kwargs={'site_slug': site.slug}), request)
    return _absolute(reverse('privacy'), request)


# ── Rendering ────────────────────────────────────────────────────────────────

def _markdown_to_mjml(text):
    """A deliberately small Markdown subset, rendered as MJML blocks.

    Not a full Markdown implementation and not trying to be. Campaign bodies are headings,
    paragraphs, links, emphasis and the occasional image or button — and every construct
    supported here has to survive Outlook, which rules out most of what a general Markdown
    renderer would emit.
    """
    blocks = []
    for chunk in re.split(r'\n\s*\n', (text or '').strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith('!['):                      # ![alt](url) on its own line
            match = re.match(r'!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)', chunk)
            if match:
                blocks.append(
                    f'<mj-image src="{escape(match.group("src"))}" '
                    f'alt="{escape(match.group("alt"))}" padding="8px 0" />')
                continue
        if chunk.startswith('[[') and chunk.endswith(']]'):   # [[Label|url]] → a button
            inner = chunk[2:-2]
            label, _, href = inner.partition('|')
            blocks.append(
                f'<mj-button href="{escape(href.strip())}" background-color="#198754" '
                f'color="#ffffff" padding="16px 0">{escape(label.strip())}</mj-button>')
            continue
        html = _inline_markdown(chunk)
        if chunk.startswith('###'):
            blocks.append(f'<mj-text font-size="18px" font-weight="bold" padding="12px 0 4px">'
                          f'{_inline_markdown(chunk.lstrip("#").strip())}</mj-text>')
        elif chunk.startswith('##'):
            blocks.append(f'<mj-text font-size="22px" font-weight="bold" padding="16px 0 4px">'
                          f'{_inline_markdown(chunk.lstrip("#").strip())}</mj-text>')
        elif chunk.startswith('#'):
            blocks.append(f'<mj-text font-size="26px" font-weight="bold" padding="20px 0 4px">'
                          f'{_inline_markdown(chunk.lstrip("#").strip())}</mj-text>')
        else:
            blocks.append(f'<mj-text padding="8px 0">{html}</mj-text>')
    body = '\n        '.join(blocks)
    return mark_safe(
        f'<mj-section padding="8px 24px">\n      <mj-column>\n        {body}\n'
        f'      </mj-column>\n    </mj-section>')


def _inline_markdown(text):
    # Escaped first, then the markdown patterns insert real tags. Campaign authors are
    # staff, but "staff" is not "trusted to hand-write MJML by accident" — an unescaped
    # angle bracket in a body would otherwise land in the markup and break the compile.
    text = escape(text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color:#198754">\1</a>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', text)
    return text.replace('\n', '<br />')


def render_campaign(campaign, subscription, request=None, extra_context=None):
    """The compiled HTML one subscriber will receive."""
    from mjml import mjml2html

    site = campaign.site
    context = {
        'campaign': campaign,
        'subject': campaign.subject,
        'preheader': campaign.preheader,
        'site': site,
        'site_name': site.name if site else 'reset.art',
        'site_icon_url': (site.icon_sm.url if site and site.icon else ''),
        'postal_address': (site.formatted_address.replace('\n', ', ') if site else ''),
        'subscriber': subscription.subscriber,
        'subscription': subscription,
        'unsubscribe_url': unsubscribe_url(subscription, request),
        'privacy_url': privacy_url(site, request),
    }
    context.update(extra_context or {})

    if campaign.template_name:
        body_mjml = render_to_string(
            f'email/campaigns/{campaign.template_name}', context, request=request)
    else:
        body_mjml = _markdown_to_mjml(campaign.body_markdown)

    # Both sources are already safe strings — render_to_string returns one, and
    # _markdown_to_mjml marks its own output. That matters: without it Django escapes the
    # body into the shell and MJML compiles the escaped text as content, so the campaign
    # arrives as a wall of &lt;mj-text&gt; rather than failing loudly.
    context['body_mjml'] = body_mjml
    shell = render_to_string('email/campaign_base.mjml', context, request=request)
    return mjml2html(shell)


def render_preview(campaign, request=None):
    """Compiled HTML for the preview page, using a stand-in recipient.

    Unsaved on purpose: previewing must not create a subscriber, and it must not send
    anything. The token in the preview's unsubscribe link will not resolve, which is correct
    — nothing should be unsubscribable from a preview.
    """
    stand_in = Subscription(
        pk=0, site=campaign.site,
        subscriber=Subscriber(pk=0, email='preview@example.com',
                              first_name='Preview', last_name='Recipient'))
    return render_campaign(campaign, stand_in, request=request)


# ── Sending ──────────────────────────────────────────────────────────────────

def recipients(campaign):
    """Everyone this campaign goes to.

    is_subscribed is the only gate, and it is applied here rather than at the call site so
    there is exactly one place that can get it wrong.
    """
    queryset = (Subscription.objects
                .select_related('subscriber', 'site')
                .filter(is_subscribed=True))
    if campaign.site_id:
        queryset = queryset.filter(site_id=campaign.site_id)
    else:
        queryset = queryset.filter(site__isnull=True)
    return queryset.order_by('pk')


def _connection():
    """Campaigns go via Resend; transactional mail stays on its own provider.

    Deliberately not EMAIL_BACKEND. A shared provider means a spam complaint about a
    newsletter suppresses that address account-wide, which would silently swallow an
    artist's acceptance email.
    """
    return get_connection('anymail.backends.resend.EmailBackend')


def build_message(campaign, subscription, request=None, connection=None, test=False):
    html = render_campaign(campaign, subscription, request=request)
    subject = f'[TEST] {campaign.subject}' if test else campaign.subject
    message = EmailMultiAlternatives(
        subject=subject,
        body=strip_tags(html),
        to=[subscription.subscriber.email],
        connection=connection,
    )
    message.attach_alternative(html, 'text/html')
    # RFC 8058. Gmail and Yahoo require one-click unsubscribe from bulk senders, and the
    # POST variant must work without a confirmation step — see the unsubscribe view.
    url = unsubscribe_url(subscription, request)
    message.extra_headers['List-Unsubscribe'] = f'<{url}>'
    message.extra_headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
    return message


def send_test(campaign, address, request=None):
    """One copy to a chosen address, which re-arms the send guard."""
    stand_in = Subscription(
        pk=0, site=campaign.site,
        subscriber=Subscriber(pk=0, email=address, first_name='Test',
                              last_name='Recipient'))
    connection = _connection()
    build_message(campaign, stand_in, request=request,
                  connection=connection, test=True).send()
    campaign.test_sent_at = timezone.now()
    campaign.save(update_fields=['test_sent_at'])
    return True


def send_campaign(campaign, request=None):
    """Send to the whole list, in batches. Returns how many were sent.

    Refuses unless a test has gone out since the last edit — the guard lives here rather
    than only in the view, so no other caller can route around it.
    """
    if not campaign.can_send:
        raise ValueError(campaign.blocked_reason or 'This campaign cannot be sent.')

    campaign.status = Campaign.STATUS_SENDING
    campaign.save(update_fields=['status'])

    sent = 0
    try:
        connection = _connection()
        connection.open()
        batch = []
        for subscription in recipients(campaign).iterator():
            batch.append(build_message(campaign, subscription, request=request,
                                       connection=connection))
            if len(batch) >= BATCH_SIZE:
                sent += connection.send_messages(batch) or 0
                batch = []
        if batch:
            sent += connection.send_messages(batch) or 0
        connection.close()
    except Exception:
        campaign.status = Campaign.STATUS_FAILED
        campaign.save(update_fields=['status'])
        logger.exception('Campaign %s failed after %s messages', campaign.pk, sent)
        raise

    campaign.status = Campaign.STATUS_SENT
    campaign.sent_at = timezone.now()
    campaign.recipient_count = sent
    campaign.save(update_fields=['status', 'sent_at', 'recipient_count'])
    return sent
