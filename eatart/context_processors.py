from gallery.permissions import is_curator_user


def navigation_roles(request):
    user = request.user
    has_artist_profile = bool(user.is_authenticated and user.artists.exists())
    return {
        'can_access_open_call_dashboard': is_curator_user(user),
        'can_access_artist_open_call': has_artist_profile,
    }
