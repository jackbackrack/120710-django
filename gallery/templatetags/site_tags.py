import nh3
from django import template
from django.urls import reverse
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


@register.filter
def sanitize(value):
    """Render user HTML safely: allow a small formatting subset, strip the rest."""
    if not value:
        return ''
    return mark_safe(nh3.clean(str(value), tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS))


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
