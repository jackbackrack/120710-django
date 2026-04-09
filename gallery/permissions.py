from django.db.models import Q

from accounts.roles import ARTIST_GROUP, CURATOR_GROUP, STAFF_GROUP


def has_group(user, group_name):
    return bool(user.is_authenticated and user.groups.filter(name=group_name).exists())


def is_staff_user(user):
    return bool(user.is_authenticated and (user.is_superuser or user.is_staff or has_group(user, STAFF_GROUP)))


def is_curator_user(user):
    return bool(user.is_authenticated and (is_staff_user(user) or has_group(user, CURATOR_GROUP)))


def is_artist_user(user):
    return bool(user.is_authenticated and (has_group(user, ARTIST_GROUP) or is_curator_user(user)))


def can_manage_artist(user, artist):
    return bool(user.is_authenticated and (is_staff_user(user) or artist.user_id == user.id))


def can_manage_artwork(user, artwork):
    return bool(
        user.is_authenticated and (
            is_staff_user(user)
            or artwork.created_by_id == user.id
            or artwork.artists.filter(user=user).exists()
        )
    )


def can_manage_show(user, show):
    return bool(user.is_authenticated and (is_staff_user(user) or show.managing_curator_id == user.id))


def can_manage_event(user, event):
    manager_id = event.managing_curator_id or event.show.managing_curator_id
    return bool(user.is_authenticated and (is_staff_user(user) or manager_id == user.id))


def visible_artwork_queryset(user):
    if is_curator_user(user):
        return Q()

    visibility_filter = Q(is_public=True)
    if user.is_authenticated:
        visibility_filter |= Q(created_by=user) | Q(artists__user=user)
    return visibility_filter


def tag_filter_queryset(queryset, tag_slug):
    if not tag_slug:
        return queryset
    return queryset.filter(tags__slug=tag_slug)
