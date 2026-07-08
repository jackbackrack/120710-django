import re

from gallery.permissions import is_staff_user

_SITE_SLUG_RE = re.compile(r'^/site/([a-z0-9]+(?:-[a-z0-9]+)*)/')


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

    current_site = None
    m = _SITE_SLUG_RE.match(request.path)
    if m:
        from gallery.models.sites import Site
        current_site = Site.objects.filter(slug=m.group(1)).first()

    return {
        'is_staff_user': bool(is_authenticated and is_staff_user(user)),
        'my_artist_url': my_artist_url,
        'my_artist_name': my_artist_name,
        'saved_artwork_ids': saved_ids,
        'current_site': current_site,
    }
