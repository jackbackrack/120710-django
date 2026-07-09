from django import template
from django.urls import reverse

register = template.Library()


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
