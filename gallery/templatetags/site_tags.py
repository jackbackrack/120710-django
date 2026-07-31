import html
import re

import nh3
from django import template
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe

register = template.Library()

# Rich-text fields (artist bio/statement, show/event/site descriptions) are user
# editable and were previously rendered with |safe, which is stored XSS. Sanitize
# them to a safe subset of formatting tags instead — keeps links/bold/lists, drops
# <script>, event handlers, and dangerous URL schemes.
_ALLOWED_TAGS = {
    'a', 'b', 'i', 'em', 'strong', 'u', 'p', 'br', 'span',
    'ul', 'ol', 'li', 'blockquote', 'h3', 'h4', 'h5', 'code', 'pre',
}
# nh3 manages the `rel` attribute on links itself (adds noopener noreferrer).
_ALLOWED_ATTRS = {'a': {'href', 'title', 'target'}}


# A second, wider allowlist for staff-authored site copy (Site.about, Site.visit_notes).
# Deliberately not merged into _ALLOWED_TAGS: that one governs artist-editable bios and
# statements, where <img> would permit tracking pixels and hotlinking from anyone with an
# account. These fields are only editable through SiteForm, which is staff-only.
_RICH_TAGS = _ALLOWED_TAGS | {
    'h1', 'h2', 'h6', 'hr', 'img', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
    'figure', 'figcaption', 'small', 'div',
}
_RICH_ATTRS = dict(_ALLOWED_ATTRS)
_RICH_ATTRS['img'] = {'src', 'alt', 'width', 'height', 'loading'}
_RICH_ATTRS['th'] = {'scope', 'colspan', 'rowspan'}
_RICH_ATTRS['td'] = {'colspan', 'rowspan'}


# "/static/img/x.png" in stored copy, resolved at render time. The content in these fields
# came from templates that used {% static %}, and it has to keep working in both
# environments: local dev serves un-hashed files from /static/, while production serves
# content-hashed names from S3 behind CloudFront. A literal /static/... path is correct in
# exactly one of those, so the reference is re-resolved on every render instead.
_STATIC_SRC_RE = re.compile(r'(?P<attr>src|href)="/static/(?P<path>[^"]+)"')


def _resolve_static(match):
    try:
        return f'{match.group("attr")}="{static(match.group("path"))}"'
    except ValueError:
        # ManifestStaticFilesStorage raises for a file it has never seen. A stale reference
        # should leave a broken image on one page, not a 500 on the whole page.
        return match.group(0)


@register.filter
def sanitize_rich(value):
    """Render staff-authored site copy: the formatting subset plus headings, tables, images.

    Also re-resolves /static/ references, so an image bundled with the app survives the
    move to hashed filenames on S3 — see _STATIC_SRC_RE above.

    nh3 still strips event handlers and dangerous URL schemes, so the widening is about
    which *elements* are permitted, not about trusting the input.
    """
    if not value:
        return ''
    cleaned = nh3.clean(str(value), tags=_RICH_TAGS, attributes=_RICH_ATTRS)
    return mark_safe(_STATIC_SRC_RE.sub(_resolve_static, cleaned))


@register.filter
def sanitize(value):
    """Render user HTML safely: allow a small formatting subset, strip the rest."""
    if not value:
        return ''
    return mark_safe(nh3.clean(str(value), tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS))


@register.filter
def get_item(mapping, key):
    """Look a key up in a dict from a template: {{ mapping|get_item:key }}.

    Django templates cannot subscript by a variable key, and rendering a list of
    shows needs a per-show value (its submission call to action) computed once in the
    view rather than re-derived per card.
    """
    if not hasattr(mapping, 'get'):
        return None
    return mapping.get(key)


@register.simple_tag(takes_context=True)
def surl(context, obj):
    """Return a site-scoped URL for obj when current_site is active, else the canonical URL."""
    current_site = context.get('current_site')
    if not current_site:
        return obj.get_absolute_url()
    from gallery.models import Show, Artist, Artwork
    if isinstance(obj, Show):
        return reverse('gallery:site_show_detail', kwargs={'site_slug': current_site.slug, 'slug': obj.slug})
    if isinstance(obj, Artist):
        return reverse('gallery:site_artist_detail', kwargs={'site_slug': current_site.slug, 'slug': obj.slug})
    if isinstance(obj, Artwork):
        return reverse('gallery:site_artwork_detail', kwargs={'site_slug': current_site.slug, 'slug': obj.slug})
    return obj.get_absolute_url()


# Elements that end a line of prose. Everything else is inline and should not gain a break.
_BLOCK_END_RE = re.compile(
    r'</\s*(?:p|div|li|ul|ol|h[1-6]|blockquote|tr|table|section|article)\s*>|<\s*br\s*/?\s*>',
    re.IGNORECASE)


@register.filter
def text_blocks(value):
    """Rich text as plain lines, with the paragraph breaks kept.

    `striptags` on its own removes the tags and nothing else, so two paragraphs come out as one
    run-on line: "<p>Street parking available</p><p>Nearby AC Transit…</p>" became "Street
    parking available Nearby AC Transit…", which reads as a single mangled sentence. The
    information was all there and the shape of it was gone.

    Used where rich text has to become text — the body of an email, an MJML block — as
    `{{ value|text_blocks|linebreaksbr }}`. On a page use `sanitize_rich` instead and keep the
    real markup.
    """
    if not value:
        return ''
    text = _BLOCK_END_RE.sub('\n', str(value))
    text = strip_tags(text)
    text = html.unescape(text)
    lines = [' '.join(line.split()) for line in text.splitlines()]
    return '\n'.join(line for line in lines if line)


@register.simple_tag
def rsvp_choices():
    """The three answers with their styling, so the two places offering them cannot drift.

    They had drifted already: the event page called "can't make it" secondary and the
    change-your-mind page called it danger, and "coming" was outlined on one and filled on the
    other — the same question asked twice in two visual languages.

    Presentation lives here rather than on the model, but the labels come from the model so the
    wording has one home.
    """
    from gallery.models import EventRsvp

    style = {EventRsvp.YES: 'success', EventRsvp.MAYBE: 'secondary', EventRsvp.NO: 'secondary'}
    return [{'value': value, 'label': label, 'style': style.get(value, 'secondary')}
            for value, label in EventRsvp.RESPONSE_CHOICES]


@register.filter
def short_date(day):
    """A date without the year, unless it is not this year. See gallery/timeranges.py."""
    from gallery import timeranges
    return timeranges.short_date(day)
