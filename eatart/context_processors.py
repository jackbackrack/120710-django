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
    current_site = None
    m = _SITE_SLUG_RE.match(request.path)
    if m:
        current_site = Site.objects.filter(slug=m.group(1)).first()
    elif _DEFAULT_SITE_SLUG:
        current_site = Site.objects.filter(
            slug=_DEFAULT_SITE_SLUG, status=Site.STATUS_PUBLISHED
        ).first()

    # Three site variables, because they answer three different questions.
    #
    #   current_site  — what the URL is scoped to. Drives `surl`, so it must be None on
    #                   network pages or every card would link to /site/<default>/... .
    #   default_site  — the deployment's own identity, whatever the URL says. This is the
    #                   umbrella (reset.art) once the network cutover happens.
    #   info_site     — where the public info pages read their content from: the scoped
    #                   site if there is one, else the default.
    #
    # Today current_site falls back to the default anyway, so info_site equals it. The
    # distinction only starts to matter at the network cutover, when the fallback above
    # goes away and current_site becomes None at the root — which is exactly when
    # /about/ still needs to render something. See docs/reset-art-network.md.
    default_site = None
    if _DEFAULT_SITE_SLUG:
        default_site = Site.objects.filter(
            slug=_DEFAULT_SITE_SLUG, status=Site.STATUS_PUBLISHED
        ).first()

    return {
        'default_site': default_site,
        'info_site': current_site or default_site,
        'is_staff_user': bool(is_authenticated and is_staff_user(user)),
        'my_artist_url': my_artist_url,
        'my_artist_name': my_artist_name,
        'saved_artwork_ids': saved_ids,
        'current_site': current_site,
    }
