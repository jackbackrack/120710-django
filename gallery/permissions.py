import datetime

from django.db.models import Q

from accounts.roles import ARTIST_GROUP, CURATOR_GROUP, JUROR_GROUP, STAFF_GROUP


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
    if not user.is_authenticated:
        return False
    if is_staff_user(user):
        return True
    return is_curator_user(user) and show.curators.filter(user=user).exists()


def can_manage_event(user, event):
    return can_manage_show(user, event.show)


def visible_artwork_queryset(user):
    if is_curator_user(user):
        return Q()
    # Visible if in a show that has already started (current or past), or staff override
    public = Q(shows__start__lte=datetime.date.today()) | Q(is_public=True)
    if user.is_authenticated:
        public |= Q(created_by=user) | Q(artists__user=user)
    return public


def visible_artist_queryset(user):
    if is_curator_user(user):
        return Q()
    # Visible if they have artwork in a show that has already started, or staff override
    public = Q(artworks__shows__start__lte=datetime.date.today()) | Q(is_public=True)
    if user.is_authenticated:
        public |= Q(user=user)
    return public


def tag_filter_queryset(queryset, tag_slug):
    if not tag_slug:
        return queryset
    return queryset.filter(tags__slug=tag_slug)


def is_juror_user(user):
    return bool(user.is_authenticated and has_group(user, JUROR_GROUP))


def is_juror_for_show(user, show):
    """Returns True if the user is assigned as a juror for the given show."""
    if not user.is_authenticated:
        return False
    return show.jurors.filter(user=user).exists()


def can_view_reviews(user, show):
    """Show managers/staff and assigned jurors can view reviews for a show."""
    return bool(
        user.is_authenticated
        and (can_manage_show(user, show) or is_juror_for_show(user, show))
    )
