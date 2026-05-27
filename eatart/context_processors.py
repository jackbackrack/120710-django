from gallery.permissions import is_artist_user, is_curator_user, is_staff_user


def navigation_roles(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(getattr(user, 'is_authenticated', False))
    has_artist_profile = bool(is_authenticated and user.artists.exists())
    return {
        'can_access_artist_open_call': has_artist_profile,
        'needs_artist_profile': bool(is_authenticated and is_artist_user(user) and not has_artist_profile),
        'is_staff_user': bool(is_authenticated and is_staff_user(user)),
    }
