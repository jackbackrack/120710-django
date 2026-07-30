import re

from django.conf import settings

from gallery.permissions import is_staff_user

_SITE_SLUG_RE = re.compile(r'^/site/([a-z0-9]+(?:-[a-z0-9]+)*)/')
_DEFAULT_SITE_SLUG = getattr(settings, 'GALLERY_DEFAULT_SITE_SLUG', None)


def navigation_roles(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(getattr(user, 'is_authenticated', False))
    my_artist_url = None
    my_artist_name = None
    saved_ids = set()
    if is_authenticated:
        from gallery.models.people import Artist
        from gallery.models.collection import SavedArtwork
        artist = Artist.objects.filter(user=user).first()
        if artist:
            my_artist_url = artist.get_absolute_url()
            my_artist_name = artist.name
        saved_ids = set(
            SavedArtwork.objects.filter(user=user).values_list('artwork_id', flat=True)
        )

    from gallery.models.sites import Site

    # One Site query per request, not two. current_site and default_site are the same row
    # whenever the path is not site-scoped, or is scoped to the default venue — which is
    # every request on a single-venue deployment. Resolved together because this runs on
    # literally every rendered response, /robots.txt included.
    current_site = None
    default_site = None
    m = _SITE_SLUG_RE.match(request.path)
    path_slug = m.group(1) if m else None

    if path_slug and path_slug != _DEFAULT_SITE_SLUG:
        current_site = Site.objects.filter(slug=path_slug).first()
        if _DEFAULT_SITE_SLUG:
            default_site = Site.objects.filter(
                slug=_DEFAULT_SITE_SLUG, status=Site.STATUS_PUBLISHED).first()
    elif _DEFAULT_SITE_SLUG:
        default_site = Site.objects.filter(
            slug=_DEFAULT_SITE_SLUG, status=Site.STATUS_PUBLISHED).first()
        # Scoped to the default venue, or falling back to it — the same row either way.
        # The fallback for an unscoped path goes away at the network cutover; see
        # docs/reset-art-network.md.
        current_site = default_site
    elif path_slug:
        current_site = Site.objects.filter(slug=path_slug).first()

    return {
        'default_site': default_site,
        'info_site': current_site or default_site,
        'is_staff_user': bool(is_authenticated and is_staff_user(user)),
        'my_artist_url': my_artist_url,
        'my_artist_name': my_artist_name,
        'saved_artwork_ids': saved_ids,
        'current_site': current_site,
    }
