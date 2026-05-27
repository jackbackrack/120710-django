import datetime

from django.db.models import Q


def is_staff_user(user):
    return bool(user.is_authenticated and (user.is_superuser or user.is_staff))


def is_curator_user(user):
    if not user.is_authenticated:
        return False
    if is_staff_user(user):
        return True
    cached = getattr(user, '_is_curator_cache', None)
    if cached is None:
        from gallery.models.people import Artist
        cached = Artist.objects.filter(user=user, curated_shows__isnull=False).exists()
        user._is_curator_cache = cached
    return cached


def is_artist_user(user):
    if not user.is_authenticated:
        return False
    cached = getattr(user, '_is_artist_cache', None)
    if cached is None:
        from gallery.models.people import Artist
        cached = Artist.objects.filter(user=user).exists()
        user._is_artist_cache = cached
    return cached


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
    if not is_curator_user(user):
        return False
    # Use prefetch cache if available (avoids per-show query when curators are prefetched)
    curators_cache = show.__dict__.get('_prefetched_objects_cache', {})
    if 'curators' in curators_cache:
        return any(c.user_id == user.pk for c in curators_cache['curators'])
    return show.curators.filter(user=user).exists()


def can_manage_event(user, event):
    return can_manage_show(user, event.show)


def visible_artwork_queryset(user):
    if is_curator_user(user):
        return Q()
    public = Q(shows__start__lte=datetime.date.today())
    if user.is_authenticated:
        public |= Q(created_by=user) | Q(artists__user=user)
    return public


def visible_artist_queryset(user):
    if is_curator_user(user):
        return Q()
    public = Q(artworks__shows__start__lte=datetime.date.today()) | Q(curated_shows__isnull=False)
    if user.is_authenticated:
        public |= Q(user=user)
    return public


def tag_filter_queryset(queryset, tag_slug):
    if not tag_slug:
        return queryset
    return queryset.filter(tags__slug=tag_slug)


def is_juror_user(user):
    if not user.is_authenticated:
        return False
    cached = getattr(user, '_is_juror_cache', None)
    if cached is None:
        from reviews.models import ShowJuror
        cached = ShowJuror.objects.filter(user=user).exists()
        user._is_juror_cache = cached
    return cached


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
