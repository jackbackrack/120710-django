from gallery.permissions import is_staff_user


def navigation_roles(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(getattr(user, 'is_authenticated', False))
    return {
        'is_staff_user': bool(is_authenticated and is_staff_user(user)),
    }
