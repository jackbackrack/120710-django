from gallery.permissions import is_staff_user


def navigation_roles(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(getattr(user, 'is_authenticated', False))
    my_artist_url = None
    if is_authenticated:
        from gallery.models.people import Artist
        artist = Artist.objects.filter(user=user).first()
        if artist:
            my_artist_url = artist.get_absolute_url()
    return {
        'is_staff_user': bool(is_authenticated and is_staff_user(user)),
        'my_artist_url': my_artist_url,
    }
