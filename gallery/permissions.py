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


def can_delete_artist(user, artist):
    if not can_manage_artist(user, artist):
        return False
    if is_staff_user(user):
        return True
    return not artist.artworks.filter(shows__isnull=False).exists()


def can_manage_artwork(user, artwork):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if artwork.created_by_id == user.id or artwork.artists.filter(user=user).exists():
        return True
    if is_curator_user(user):
        return artwork.shows.filter(curators__user=user).exists()
    return False


def can_delete_artwork(user, artwork):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if artwork.created_by_id != user.id and not artwork.artists.filter(user=user).exists():
        return False
    published_statuses = {'published', 'closed'}
    return not artwork.shows.filter(status__in=published_statuses).exists()


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


PUBLISHED_SHOW_STATUSES = ['published', 'closed']


def _published_show_ids():
    from gallery.models import Show
    return Show.objects.filter(status__in=PUBLISHED_SHOW_STATUSES).values('pk')


def _curator_show_ids(user):
    from gallery.models import Show
    return Show.objects.filter(curators__user=user).values('pk')


def visible_show_queryset(qs, user):
    from gallery.models import Show
    if is_staff_user(user):
        return qs
    if is_juror_user(user):
        return qs
    if is_curator_user(user):
        return qs.filter(Q(status__in=Show.PUBLIC_STATUSES) | Q(pk__in=_curator_show_ids(user)))
    return qs.filter(status__in=Show.PUBLIC_STATUSES)


def visible_artwork_queryset(user):
    if is_staff_user(user):
        return Q()
    if is_curator_user(user):
        q = Q(shows__in=_curator_show_ids(user)) | Q(shows__in=_published_show_ids())
        q |= Q(created_by=user) | Q(artists__user=user)
        return q
    public = Q(shows__in=_published_show_ids())
    if user.is_authenticated:
        public |= Q(created_by=user) | Q(artists__user=user)
    return public


def visible_artist_queryset(user):
    if is_staff_user(user):
        return Q()
    if is_curator_user(user):
        q = (Q(artworks__shows__in=_curator_show_ids(user))
             | Q(artworks__shows__in=_published_show_ids())
             | Q(curated_shows__isnull=False))
        q |= Q(user=user)
        return q
    public = Q(artworks__shows__in=_published_show_ids()) | Q(curated_shows__isnull=False)
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


def can_see_all_shows(user):
    return is_staff_user(user) or is_juror_user(user)


def can_view_reviews(user, show):
    """Show managers/staff and assigned jurors can view reviews for a show."""
    return bool(
        user.is_authenticated
        and (can_manage_show(user, show) or is_juror_for_show(user, show))
    )
