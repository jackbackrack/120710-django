from gallery.permissions import is_curator_user


def navigation_roles(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(getattr(user, 'is_authenticated', False))
    has_artist_profile = bool(is_authenticated and user.artists.exists())
    return {
        'can_access_open_call_dashboard': bool(is_authenticated and is_curator_user(user)),
        'can_access_artist_open_call': has_artist_profile,
    }
