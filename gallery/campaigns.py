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
import threading
import time

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import connection as db_connection, models
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, strip_tags
from django.utils.safestring import mark_safe

from gallery.models import (Campaign, CampaignDelivery, Subscriber,
                            Subscription)

logger = logging.getLogger(__name__)

# How many subscriptions are loaded from the database at a time. Not a batch API call: the
# backend makes one request per message, since each recipient's mail differs — the unsubscribe
# link is per-person. This only bounds how much is held in memory and how often the progress
# clock is moved.
BATCH_SIZE = 100

# Per-message retries before giving up on an address, and the first backoff in seconds
# (doubling each time). A rate limit or a provider blip should cost a pause, not the campaign.
SEND_ATTEMPTS = 3
RETRY_BACKOFF = 2

# Stop rather than work through the remaining list against a provider that is refusing
# everything. Enough that a couple of bad addresses in a row do not halt a send.
ABORT_AFTER_CONSECUTIVE_FAILURES = 10

# How often a running send says it is still alive. Comfortably inside Campaign.STALL_AFTER, so
# a healthy send is never mistaken for an abandoned one however slowly it is being throttled.
PROGRESS_EVERY_SECONDS = 30


def messages_per_second():
    """Read at send time so tests and an operator can change it without a restart."""
    return getattr(settings, 'CAMPAIGN_MESSAGES_PER_SECOND', 2)

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


# ── Templates ────────────────────────────────────────────────────────────────

# The recurring formats, with a readable name and what each needs to render. A template not
# listed here still works — drop a .mjml file in templates/email/campaigns/ and it is offered —
# it just gets its filename as a label and is assumed to need nothing.
CAMPAIGN_TEMPLATES = {
    'show_announcement.mjml': {
        'label': 'Show announcement — a show is coming',
        'needs': ('show',),
        'subject': 'Coming up: {{ show.name }}',
    },
    'show_opening.mjml': {
        'label': 'Show opening — dates, reception, a few works',
        'needs': ('show',),
        # `opening` is the first event; a show with none falls back to its own start date, so
        # the subject never reads "Opening: X — " with nothing after it.
        # The time as well as the day: an opening is an invitation, and "Saturday" without an
        # hour is not one. Falls back to the day alone when the show has no event to take it from.
        'subject': 'Opening: {{ show.name }} — '
                   '{% if opening %}{{ opening.date|date:"l j F" }}, '
                   '{{ opening.time_range }}'
                   '{% else %}{{ show.start|date:"l j F" }}{% endif %}',
    },
    'show_closing.mjml': {
        'label': 'Closing soon — last chance to see it',
        'needs': ('show',),
        # The last day is the point of the mailing, and the closing event's time is what somebody
        # would act on today — so both, with the event named so its hour is not left dangling.
        'subject': 'Last chance: {{ show.name }} — last day {{ show.end|date:"j F" }}'
                   '{% if closing %} · {{ closing.name }} {{ closing.time_range }}'
                   '{% endif %}',
    },
}


def template_subject(name):
    return CAMPAIGN_TEMPLATES.get(name, {}).get('subject', '')


def template_label(name):
    return CAMPAIGN_TEMPLATES.get(name, {}).get('label') or name


def template_needs(name):
    return CAMPAIGN_TEMPLATES.get(name, {}).get('needs', ())


SUBJECT_MAX = 255


# Whether a venue's masthead has been confirmed to exist, keyed by site and icon file. The
# cachefile strategy is Optimistic — derived images are generated when the source is saved, and it
# skips existence checks on .url so that a page of hundreds of thumbnails is not hundreds of S3
# requests. That is right for pages and wrong here: `icon_md` was added long after these icons were
# uploaded, so it had never been generated and every campaign carried a broken image. Checked once
# per process per icon rather than once per recipient, which would be a HEAD request per message.
_LOGO_URL = {}


def campaign_logo_url(site, request=None):
    """An absolute URL for a masthead that definitely exists, or '' — never a broken image.

    A missing image in an email cannot be fixed after it is sent and looks like carelessness in
    every inbox that opens it, so the failure mode here is deliberately "no logo": the shell falls
    back to the venue's name in letterspaced caps, which looks intentional.

    Absolute, always. On S3 the storage already returns a full URL, but with local media it returns
    "/media/..." — which resolves against the mail client, not against us, and is a broken image
    everywhere.
    """
    if not (site and site.icon):
        return ''
    key = (site.pk, site.icon.name)
    if key in _LOGO_URL:
        return _LOGO_URL[key]

    url = ''
    try:
        spec = site.icon_md
        if not spec.storage.exists(spec.name):
            spec.generate()
        url = spec.url
        if not url.startswith(('http://', 'https://')):
            url = _absolute(url, request)
    except Exception:   # noqa: BLE001 — a logo is never worth failing a send over
        logger.exception('Could not prepare a masthead for site %s', getattr(site, 'pk', None))
        url = ''
    _LOGO_URL[key] = url
    return url


def subject_context(campaign, request=None):
    """The names a subject line may use. The same objects the body gets."""
    site = campaign.site
    context = {
        'campaign': campaign,
        'site': site,
        'site_name': site.name if site else 'reset.art',
    }
    if campaign.show_id:
        context.update(show_context(campaign.show, request=request))
    return context


def render_subject(campaign, request=None):
    """The subject as it will arrive, with {{ show.name }} and friends filled in.

    A subject is the one line every recipient reads, and it carries the same facts as the body —
    which show, which date. Retyping them there was the last place a mailing could contradict
    itself, saying one date in the subject and another three lines down.

    Only `{{ }}` substitution is supported; `{% %}` is rejected by the form. Django guards the
    dangerous callables itself (`Model.delete` and `Model.save` are marked `alters_data`), but
    tags like `{% load %}` and `{% include %}` are a different surface, and a one-line subject has
    no use for them.

    Never raises: a subject that will not render falls back to its own source text. The form
    catches the mistake at the point it is made, and failing a whole send over a stray brace would
    be the wrong trade at the point it is discovered.
    """
    from django.template import Context, Template, TemplateSyntaxError

    source = campaign.subject or ''
    if '{{' not in source:
        return source
    try:
        rendered = Template(source).render(Context(subject_context(campaign, request)))
    except (TemplateSyntaxError, Exception):   # noqa: BLE001 — a subject must not break a send
        logger.exception('Campaign %s subject would not render: %r', campaign.pk, source)
        return source
    return rendered.strip()[:SUBJECT_MAX]


def show_context(show, request=None):
    """Everything a show-shaped template needs, derived from the show itself.

    Openings and closings are `Event` rows rather than fields, and nothing marks which is which,
    so they are taken by date: the first event is the opening and the last is the closing. That
    is right for the ordinary case of a show with one reception at each end, and a template that
    finds neither falls back to the show's own start and end dates rather than rendering a gap.
    """
    events = list(show.events.order_by('date', 'start'))
    return {
        'show': show,
        'show_url': _absolute(show.get_absolute_url(), request),
        # Ordered as the show itself orders them, and capped in the template rather than here so
        # one query serves a template that wants three works and one that wants six.
        'artworks': list(show.artworks.all()),
        'events': events,
        'opening': events[0] if events else None,
        'closing': events[-1] if len(events) > 1 else None,
        'curators': list(show.curators.order_by('last_name', 'first_name')),
    }


# ── Rendering ────────────────────────────────────────────────────────────────

# A line that opens a bullet or a numbered item. Kept narrow on purpose: one level, no
# nesting. A newsletter needs "three things are happening", not an outline.
_BULLET = re.compile(r'^\s*[-*+]\s+(?P<item>.+)$')
_NUMBERED = re.compile(r'^\s*\d+[.)]\s+(?P<item>.+)$')

# Email clients disagree about default list indentation, and Outlook ignores margin on <ul>.
# Stating both leaves nothing to a default.
#
# inline-block so the list sits in the middle of a centred column while its items still read
# left-aligned against their bullets. Fully centring list items puts every bullet in a different
# place and is unreadable; this is what "centred" should mean for a list.
_LIST_STYLE = ('margin:0 auto;padding-left:20px;display:inline-block;text-align:left')
_ITEM_STYLE = 'padding-bottom:6px'


def _list_block(chunk):
    """A `<ul>` or `<ol>` if every line of the chunk is an item, otherwise None.

    All-or-nothing, because a chunk where only some lines look like items is far more likely
    to be a paragraph that happens to contain a dash than a list someone typed wrong.
    """
    lines = [line for line in chunk.split('\n') if line.strip()]
    if not lines:
        return None
    for tag, pattern in (('ul', _BULLET), ('ol', _NUMBERED)):
        matches = [pattern.match(line) for line in lines]
        if all(matches):
            # No line breaks inside an item: each item is already its own line, and turning
            # its newline into a <br /> would put a blank line inside every bullet.
            items = ''.join(
                f'<li style="{_ITEM_STYLE}">{_inline_markdown(m.group("item"), breaks=False)}</li>'
                for m in matches)
            return (f'<mj-text padding="8px 0">'
                    f'<{tag} style="{_LIST_STYLE}">{items}</{tag}></mj-text>')
    return None


def _markdown_blocks(text):
    """The MJML blocks for a body, without the section and column around them.

    Separate from `_markdown_to_mjml` so a campaign template can place an author's prose
    inside its own layout — see `render_campaign`.
    """
    blocks = []
    for chunk in re.split(r'\n\s*\n', (text or '').strip()):
        chunk = chunk.strip()
        if not chunk:
            continue

        if chunk.startswith('!['):                      # ![alt](url), then anything after it
            match = re.match(r'!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)', chunk)
            if match:
                blocks.append(
                    f'<mj-image src="{escape(match.group("src"))}" '
                    f'alt="{escape(match.group("alt"))}" padding="8px 0" />')
                # Whatever followed the image is a caption, and it used to be dropped on the
                # floor — silently, which is the worst way to lose someone's writing.
                rest = chunk[match.end():].strip()
                if rest:
                    blocks.append(f'<mj-text font-size="13px" color="#666666" '
                                  f'padding="0 0 8px">{_inline_markdown(rest)}</mj-text>')
                continue

        if chunk.startswith('[[') and chunk.endswith(']]'):   # [[Label|url]] → a button
            inner = chunk[2:-2]
            label, _, href = inner.partition('|')
            blocks.append(
                f'<mj-button href="{escape(href.strip())}" background-color="#198754" '
                f'color="#ffffff" padding="16px 0">{escape(label.strip())}</mj-button>')
            continue

        if chunk.startswith('###'):
            blocks.append(f'<mj-text font-size="18px" font-weight="bold" padding="12px 0 4px">'
                          f'{_inline_markdown(chunk.lstrip("#").strip())}</mj-text>')
            continue
        if chunk.startswith('##'):
            blocks.append(f'<mj-text font-size="22px" font-weight="bold" padding="16px 0 4px">'
                          f'{_inline_markdown(chunk.lstrip("#").strip())}</mj-text>')
            continue
        if chunk.startswith('#'):
            blocks.append(f'<mj-text font-size="26px" font-weight="bold" padding="20px 0 4px">'
                          f'{_inline_markdown(chunk.lstrip("#").strip())}</mj-text>')
            continue

        # Checked after the headings so a "#" line is never mistaken for anything else, and
        # before the paragraph fallback, which is what a list used to fall through to.
        listing = _list_block(chunk)
        if listing:
            blocks.append(listing)
            continue

        blocks.append(f'<mj-text padding="8px 0">{_inline_markdown(chunk)}</mj-text>')

    return '\n        '.join(blocks)


def _markdown_to_mjml(text):
    """A deliberately small Markdown subset, rendered as MJML blocks.

    Not a full Markdown implementation and not trying to be. Campaign bodies are headings,
    paragraphs, lists, links, emphasis and the occasional image or button — and every construct
    supported here has to survive Outlook, which rules out most of what a general Markdown
    renderer would emit.

    Anything unsupported renders as the literal text the author typed rather than raising. That
    is a deliberate trade: the send guard means somebody always looks at a real copy before it
    goes out, so a visible oddity gets caught, whereas a hard error on a stray character would
    block a mailing over nothing.
    """
    return mark_safe(
        f'<mj-section padding="8px 24px">\n      <mj-column>\n        '
        f'{_markdown_blocks(text)}\n      </mj-column>\n    </mj-section>')


def _inline_markdown(text, breaks=True):
    # Escaped first, then the markdown patterns insert real tags. Campaign authors are
    # staff, but "staff" is not "trusted to hand-write MJML by accident" — an unescaped
    # angle bracket in a body would otherwise land in the markup and break the compile.
    text = escape(text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color:#198754">\1</a>', text)
    # Emphasis cannot span a line break. Without that bound, a star-bulleted list read as one
    # long italic — "* one\n* two" became "<em> one</em> two" — so the one construct people
    # reach for most produced the most mangled output.
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', text)
    return text.replace('\n', '<br />') if breaks else text


def render_campaign(campaign, subscription, request=None, extra_context=None):
    """The compiled HTML one subscriber will receive."""
    from mjml import mjml2html

    site = campaign.site
    context = {
        'campaign': campaign,
        'subject': render_subject(campaign, request),
        'preheader': campaign.preheader,
        'site': site,
        'site_name': site.name if site else 'reset.art',
        # icon_md, not icon_sm: this is a masthead at ~150px, not a 32px favicon. Via the helper,
        # which makes sure the derived file actually exists before putting its URL in an email.
        #
        # Each venue's own logo, whichever venue this is. A network-wide campaign has no venue at
        # all, so it borrows the deployment's default one rather than going out bare — a
        # recognisable mark beats none. A venue that simply has no icon keeps its own wordmark:
        # showing somebody else's logo on their mail would be worse than showing none.
        'site_icon_url': campaign_logo_url(site or Subscriber.default_site(), request),
        'site_url': _absolute(site.get_absolute_url(), request) if site else '',
        'maps_url': site.maps_url if site else '',
        'postal_address': (site.formatted_address.replace('\n', ', ') if site else ''),
        'subscriber': subscription.subscriber,
        'subscription': subscription,
        'unsubscribe_url': unsubscribe_url(subscription, request),
        'privacy_url': privacy_url(site, request),
    }
    # Resolved here rather than left to the caller. It used to come only from `extra_context`,
    # which nothing in the app ever passed — so a show template rendered with every field blank
    # in the preview, in the test send and in the real send alike, and only the tests ever saw
    # it work.
    if campaign.show_id:
        context.update(show_context(campaign.show, request=request))

    context.update(extra_context or {})

    # The author's prose, offered to the template as well as used on its own. A template used
    # to *replace* the Markdown body, which meant a designed layout and editable prose were
    # mutually exclusive: staff could either write something this week or have it laid out
    # properly, and getting both took a deploy.
    #
    # Two forms, because MJML nesting is strict and the mistake is otherwise a baffling compile
    # error: `campaign_body` is a whole section, to drop between other sections, and
    # `campaign_body_blocks` is just the contents, to drop inside an existing <mj-column>.
    context['campaign_body'] = _markdown_to_mjml(campaign.body_markdown)
    context['campaign_body_blocks'] = mark_safe(_markdown_blocks(campaign.body_markdown))

    if campaign.template_name:
        body_mjml = render_to_string(
            f'email/campaigns/{campaign.template_name}', context, request=request)
    else:
        body_mjml = context['campaign_body']

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
    line = render_subject(campaign, request)
    subject = f'[TEST] {line}' if test else line
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


def pending(campaign):
    """Everyone who should get this campaign and has no delivery record for it yet.

    This — not `recipients` — is what a send iterates, which is what makes a send safe to
    run twice. A resume picks up exactly the people with no delivery record, so finishing an
    interrupted send cannot re-mail anyone it already reached.
    """
    return recipients(campaign).exclude(deliveries__campaign=campaign)


def _claim(campaign, resume=False):
    """Take exclusive ownership of sending this campaign, or refuse.

    A compare-and-set on the status column, because "check then act" is not enough here: two
    workers behind one URL, a double-clicked Send button, or an operator hitting Resume on a
    send that is actually still alive would otherwise each start their own pass over the list
    and mail everyone twice. Whichever UPDATE matches a row first owns the send; the loser
    gets False and sends nothing.

    The unique constraint on CampaignDelivery would catch the duplicate afterwards, but only
    after the mail had already gone out — too late to matter.
    """
    query = Campaign.objects.filter(pk=campaign.pk)
    if resume:
        # Either it failed, or it says sending and has stopped making progress. For the
        # latter, the observed progress_at is part of the condition, so a send that is in
        # fact still running (and has moved the timestamp since) will not be stolen.
        query = query.filter(
            models.Q(status__in=[Campaign.STATUS_FAILED, Campaign.STATUS_PAUSED])
            | models.Q(status=Campaign.STATUS_SENDING, progress_at=campaign.progress_at))
    else:
        query = query.filter(status=Campaign.STATUS_DRAFT)

    claimed = query.update(status=Campaign.STATUS_SENDING, progress_at=timezone.now())
    if claimed:
        campaign.refresh_from_db()
    return bool(claimed)


def _transient(exc):
    """Whether an error is worth trying the same message again for.

    A rate limit or a bad five minutes at the provider is transient; a malformed address is
    not, and retrying it only delays the rest of the list.
    """
    status = getattr(exc, 'status_code', None)
    if status is not None:
        return status == 429 or status >= 500
    # Network-level failures arrive without a status code at all.
    return isinstance(exc, (OSError, ConnectionError))


def _send_one(connection, message, campaign_pk, email):
    """One message, retrying a transient failure.

    Returns (sent, permanent, detail). `permanent` distinguishes the two failures that need
    completely different handling: an address the provider will never accept, versus a provider
    that is briefly unable to accept anything. Treating the second like the first would
    unsubscribe people for the provider having a bad minute.

    Retried here rather than by resuming the whole campaign, because the overwhelmingly common
    failure on a list of any size is a rate limit, and the right response to that is to wait a
    moment — not to stop the send and make somebody come and press a button.
    """
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            return bool(connection.send_messages([message])), False, ''
        except Exception as exc:   # noqa: BLE001 — classified immediately below
            if _transient(exc):
                if attempt < SEND_ATTEMPTS:
                    delay = RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning('Campaign %s: %s on attempt %s for %s, retrying in %ss',
                                   campaign_pk, type(exc).__name__, attempt, email, delay)
                    time.sleep(delay)
                    continue
                # Out of attempts on something that was never the recipient's fault. Left
                # pending so a resume tries them again.
                logger.error('Campaign %s: %s still failing after %s attempts (%s)',
                             campaign_pk, email, SEND_ATTEMPTS, exc)
                return False, False, str(exc)[:255]
            logger.error('Campaign %s: %s rejected outright (%s)', campaign_pk, email, exc)
            return False, True, str(exc)[:255]
    return False, False, ''


def send_campaign(campaign, request=None, resume=False, limit=None):
    """Send to everyone who has not had it yet. Returns how many went out on this pass.

    `limit` sends at most that many and leaves the campaign paused with the rest still owed —
    which is how a warm-up works. A domain with no sending history that puts a thousand messages
    out on its first day gets filtered on volume alone, however gently they are paced, so the
    ramp has to be across days rather than within one send. Resume, or another limited pass,
    continues from exactly where it stopped.

    Runs to completion — ten minutes for a thousand-person list, given the rate limit — so it
    belongs off the request thread. See `start_send`. Called directly it is still correct,
    just slow, which is what the management command and the tests do.

    Refuses unless a test has gone out since the last edit. The guard lives here rather than
    only in the view, so no other caller can route around it.
    """
    if resume:
        if not campaign.can_resume:
            raise ValueError('This campaign is not stopped part-way; there is nothing to '
                             'resume.')
    elif not campaign.can_send:
        raise ValueError(campaign.blocked_reason or 'This campaign cannot be sent.')

    if not _claim(campaign, resume=resume):
        raise ValueError('This campaign is already being sent.')

    # The recipient list is fixed here rather than iterated lazily, because the loop writes
    # delivery rows that would otherwise change the very queryset being walked.
    everyone = list(pending(campaign).values_list('pk', flat=True))
    remaining = everyone[:limit] if limit else everyone
    held_back = len(everyone) - len(remaining)

    sent = 0
    rejected = 0
    stalled_on = []
    consecutive = 0
    throttle = _Throttle(messages_per_second())
    last_touch = time.monotonic()

    try:
        connection = _connection()
        connection.open()
        for start in range(0, len(remaining), BATCH_SIZE):
            subscriptions = list(Subscription.objects
                                 .select_related('subscriber', 'site')
                                 .filter(pk__in=remaining[start:start + BATCH_SIZE]))
            for subscription in subscriptions:
                email = subscription.subscriber.email
                message = build_message(campaign, subscription, request=request,
                                        connection=connection)
                throttle.wait()
                ok, permanent, detail = _send_one(connection, message, campaign.pk, email)

                if ok:
                    # Recorded one at a time, immediately, because the backend makes one API
                    # call per message anyway — so exact records cost nothing, and no resume
                    # ever repeats a message.
                    _record(campaign, subscription)
                    sent += 1
                    consecutive = 0
                elif permanent:
                    # The provider will not accept this address, which is a hard bounce told to
                    # us up front instead of by webhook ten minutes later. Handled the same
                    # way: stop mailing them, and record it so a resume does not keep trying.
                    _reject(campaign, subscription, detail)
                    rejected += 1
                    consecutive += 1
                else:
                    # Not their fault. Left with no record at all, so a resume tries again.
                    stalled_on.append(email)
                    consecutive += 1

                if consecutive >= ABORT_AFTER_CONSECUTIVE_FAILURES:
                    raise RuntimeError(
                        f'{consecutive} messages in a row failed — stopping rather than '
                        f'working through the rest of the list against a provider that is '
                        f'not accepting mail.')

                # By elapsed time, not by message count. A slow rate limit could otherwise
                # leave the clock untouched for longer than STALL_AFTER, at which point a
                # perfectly healthy send looks abandoned and somebody is invited to resume it
                # alongside itself — two threads working the same list.
                if time.monotonic() - last_touch > PROGRESS_EVERY_SECONDS:
                    _touch_progress(campaign)
                    last_touch = time.monotonic()
        _touch_progress(campaign)
        connection.close()
    except Exception:
        _fail(campaign, sent)
        raise

    if rejected:
        logger.warning('Campaign %s: %s address(es) rejected and unsubscribed as bounces',
                       campaign.pk, rejected)

    if stalled_on:
        # Addresses that could not be tried properly, as opposed to ones that were refused.
        # Failed rather than sent, because these people are still owed the mailing and a
        # campaign that reported success would bury that.
        _fail(campaign, sent)
        logger.error('Campaign %s: %s address(es) could not be reached this pass: %s',
                     campaign.pk, len(stalled_on), ', '.join(stalled_on[:20]))
        return sent

    if held_back:
        # Deliberately short of the whole list. Paused rather than sent, so the page and the
        # command both keep saying that people are still waiting for it.
        campaign.status = Campaign.STATUS_PAUSED
        campaign.save(update_fields=['status'])
        logger.info('Campaign %s paused after %s message(s); %s still to go',
                    campaign.pk, sent, held_back)
        return sent

    # Rejections do not hold a campaign open. Those addresses are settled — refused, recorded,
    # and unsubscribed — so there is nothing left for a resume to do, and leaving the campaign
    # failed would mean pressing Resume forever against addresses that will never accept mail.
    campaign.status = Campaign.STATUS_SENT
    campaign.sent_at = timezone.now()
    campaign.recipient_count = campaign.sent_so_far
    campaign.save(update_fields=['status', 'sent_at', 'recipient_count'])
    return sent


def _fail(campaign, sent):
    campaign.status = Campaign.STATUS_FAILED
    campaign.save(update_fields=['status'])
    logger.error('Campaign %s stopped after %s message(s) on this pass', campaign.pk, sent)


def _record(campaign, subscription):
    """Mark one person as having received it.

    After the send, never before: a record written first would, on a crash between the two,
    convince a later resume that somebody had been mailed when they had not, and silently drop
    them from the list. Written after, the same crash costs one duplicate at most. Duplicates
    are the tolerable failure here; omissions are not.
    """
    CampaignDelivery.objects.get_or_create(
        campaign=campaign, subscription=subscription,
        defaults={'status': CampaignDelivery.STATUS_SENT})


def _reject(campaign, subscription, detail):
    """Record a refused address and stop mailing it.

    Both halves matter. The record settles the address for this campaign, so a resume does not
    retry something that will never work. The unsubscribe settles it for every future campaign,
    which is the same thing the bounce webhook does — a provider refusing an address outright is
    a hard bounce, whether it tells us synchronously or ten minutes later.
    """
    CampaignDelivery.objects.get_or_create(
        campaign=campaign, subscription=subscription,
        defaults={'status': CampaignDelivery.STATUS_REJECTED, 'error': detail})
    subscription.subscriber.unsubscribe_all(reason=Subscription.UNSUB_BOUNCED)


def _touch_progress(campaign):
    """Move the clock that `is_stalled` reads. Per batch, not per message — it exists to show
    that the send is alive, and a write per message would be a thousand pointless updates."""
    Campaign.objects.filter(pk=campaign.pk).update(progress_at=timezone.now())


class _Throttle:
    """Keeps sends under the provider's rate limit.

    Resend's default allowance is a couple of requests a second, and the backend makes one
    request per message. Without this, a list of any size trips a 429 within the first few
    seconds — which is precisely the failure that would look like the mailing list being
    broken, on the one day it matters.
    """

    def __init__(self, per_second):
        self.interval = 1.0 / per_second if per_second else 0.0
        self.next_at = 0.0

    def wait(self):
        if not self.interval:
            return
        now = time.monotonic()
        if now < self.next_at:
            time.sleep(self.next_at - now)
        self.next_at = max(now, self.next_at) + self.interval


def start_send(campaign, resume=False):
    """Begin a send in a background thread and return at once.

    A campaign send is minutes of work — a thousand messages at a hundred per batch — and
    doing it in the request would hold a worker for the duration and hand the operator a
    gateway timeout long before it finished. Worse, the send would carry on invisibly behind
    the error page, so the one person who needed to know how it went was the one person who
    could not find out.

    No queue is involved, so this thread dies with the process. That is survivable only
    because of the delivery records: a killed send leaves a campaign marked sending with a
    stale progress clock, `can_resume` notices, and Resume carries on from the right place.
    Without them this would be reckless.

    Deliberately no `request`: holding one past the response is a bug waiting to happen, and
    the link builder already falls back to SITE_BASE_URL without one.
    """
    if not getattr(settings, 'CAMPAIGN_SEND_IN_BACKGROUND', True):
        # Tests, and anyone who wants the exception rather than a log line. A thread would
        # also be invisible to a TestCase's transaction, which it could not see anyway.
        send_campaign(campaign, resume=resume)
        return None

    def run():
        try:
            send_campaign(campaign, resume=resume)
        except Exception:   # noqa: BLE001 — already logged, and recorded as FAILED
            logger.exception('Background send of campaign %s ended badly', campaign.pk)
        finally:
            # The thread got its own connection; leaving it open leaks one per send.
            db_connection.close()

    thread = threading.Thread(target=run, name=f'campaign-send-{campaign.pk}', daemon=True)
    thread.start()
    return thread
