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


def _is_gallery_admin(user):
    """Staff user who has no artist-curator profile — a real gallery admin, not a curator."""
    if not (user.is_authenticated and user.is_staff and not user.is_superuser):
        return False
    cached = getattr(user, '_is_gallery_admin_cache', None)
    if cached is None:
        from gallery.models.people import Artist
        cached = not Artist.objects.filter(user=user, curated_shows__isnull=False).exists()
        user._is_gallery_admin_cache = cached
    return cached


def can_manage_artist(user, artist):
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_gallery_admin(user):
        return True
    return artist.user_id == user.id


def can_delete_artist(user, artist):
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_gallery_admin(user):
        return True
    if artist.user_id != user.id:
        return False
    return not artist.artworks.filter(shows__start__lte=datetime.date.today()).exists()


def can_manage_artwork(user, artwork):
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_gallery_admin(user):
        return True
    if artwork.created_by_id == user.id or any(a.user_id == user.id for a in artwork.artists.all()):
        return True
    if is_curator_user(user):
        return any(
            any(c.user_id == user.id for c in s.curators.all())
            for s in artwork.shows.all()
        )
    return False


def can_delete_artwork(user, artwork):
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_gallery_admin(user):
        return True
    if artwork.created_by_id != user.id and not any(a.user_id == user.id for a in artwork.artists.all()):
        return False
    today = datetime.date.today()
    return not any(s.start <= today for s in artwork.shows.all())


def can_manage_show(user, show):
    if not user.is_authenticated:
        return False
    if user.is_superuser or _is_gallery_admin(user):
        return True
    if not is_curator_user(user):
        return False
    # Use prefetch cache if available (avoids per-show query when curators are prefetched)
    curators_cache = show.__dict__.get('_prefetched_objects_cache', {})
    if 'curators' in curators_cache:
        return any(c.user_id == user.pk for c in curators_cache['curators'])
    return show.curators.filter(user=user).exists()


def can_delete_show(user, show):
    if not user.is_authenticated:
        return False
    return user.is_superuser or _is_gallery_admin(user)


def can_manage_event(user, event):
    return can_manage_show(user, event.show)


PUBLISHED_SHOW_STATUSES = ['published', 'closed']


def _published_show_ids():
    from gallery.models import Show
    return Show.objects.filter(status__in=PUBLISHED_SHOW_STATUSES).values('pk')


def _curator_show_ids(user):
    from gallery.models import Show
    return Show.objects.filter(curators__user=user).values('pk')


def _juror_show_ids(user):
    from reviews.models import ShowJuror
    return ShowJuror.objects.filter(user=user).values('show_id')


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
        # Curators (and any show they also jury) can view artworks submitted to
        # their shows before those submissions are accepted into the show — so the
        # review-slideshow "detail" link works during curation/review.
        q |= Q(submissions__show__in=_curator_show_ids(user))
        if is_juror_user(user):
            q |= Q(submissions__show__in=_juror_show_ids(user))
        return q
    public = Q(shows__in=_published_show_ids())
    if user.is_authenticated:
        public |= Q(created_by=user) | Q(artists__user=user)
        if is_juror_user(user):
            public |= Q(submissions__show__in=_juror_show_ids(user))
    return public


def visible_artist_queryset(user):
    if is_staff_user(user):
        return Q()
    if is_curator_user(user):
        q = (Q(artworks__shows__in=_curator_show_ids(user))
             | Q(artworks__shows__in=_published_show_ids())
             | Q(curated_shows__isnull=False)
             | Q(user__collection_pieces__status='confirmed'))
        q |= Q(user=user)
        return q
    public = (Q(artworks__shows__in=_published_show_ids())
              | Q(curated_shows__isnull=False)
              | Q(user__collection_pieces__status='confirmed'))
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


def _user_emails(user):
    """Every email that identifies this user: the account email, any allauth
    EmailAddress rows, and the emails on their artist profile(s)."""
    emails = set()
    if getattr(user, 'email', ''):
        emails.add(user.email.strip().lower())
    try:
        from allauth.account.models import EmailAddress
        for ea in EmailAddress.objects.filter(user=user).values_list('email', flat=True):
            if ea:
                emails.add(ea.strip().lower())
    except Exception:
        pass
    for a_email in user.artists.values_list('email', flat=True):
        if a_email:
            emails.add(a_email.strip().lower())
    return emails


def invited_show_ids(user):
    """Show ids the user has an invitation to — matched by ANY of the user's email
    addresses OR by an artist profile linked to the user. Curators may invite by an
    email that differs from the artist's login email, so matching only on
    user.email misses those invitations."""
    if not user.is_authenticated:
        return set()
    from gallery.models.exhibitions import ShowInvitation
    q = Q(artist__user=user) | Q(claimed_by=user)   # claimed via the invite link
    artist_ids = list(user.artists.values_list('id', flat=True))
    if artist_ids:
        q |= Q(artist_id__in=artist_ids)
    emails = _user_emails(user)
    if emails:
        q |= Q(email__in=emails)
    return set(ShowInvitation.objects.filter(q).values_list('show_id', flat=True))


def user_invited_to_show(show, user):
    """True if the user has an invitation to this show (robust to email mismatch)."""
    if not user.is_authenticated:
        return False
    return show.id in invited_show_ids(user)


def can_see_all_shows(user):
    return is_staff_user(user) or is_juror_user(user)


def can_view_reviews(user, show):
    """Show managers/staff and assigned jurors can view reviews for a show."""
    return bool(
        user.is_authenticated
        and (can_manage_show(user, show) or is_juror_for_show(user, show))
    )
