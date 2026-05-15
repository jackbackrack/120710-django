from django.contrib.auth.models import Group


ARTIST_GROUP = 'artist'
CURATOR_GROUP = 'curator'
STAFF_GROUP = 'staff'
JUROR_GROUP = 'juror'


def ensure_role_groups():
    groups = {}
    for name in (ARTIST_GROUP, CURATOR_GROUP, STAFF_GROUP, JUROR_GROUP):
        groups[name], _ = Group.objects.get_or_create(name=name)
    return groups


def add_artist_role(user):
    groups = ensure_role_groups()
    user.groups.add(groups[ARTIST_GROUP])


def add_curator_role(user):
    groups = ensure_role_groups()
    user.groups.add(groups[ARTIST_GROUP], groups[CURATOR_GROUP])

    # Set artist profile public if exists
    from gallery.models.people import Artist
    try:
        artist = Artist.objects.get(user=user)
        if not artist.is_public:
            artist.is_public = True
            artist.save(update_fields=["is_public"])
    except Artist.DoesNotExist:
        pass


def remove_curator_role(user):
    groups = ensure_role_groups()
    user.groups.remove(groups[CURATOR_GROUP])


def add_staff_role(user):
    groups = ensure_role_groups()
    user.groups.add(groups[STAFF_GROUP])
    if not user.is_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])


def add_juror_role(user):
    groups = ensure_role_groups()
    user.groups.add(groups[JUROR_GROUP])


def remove_juror_role(user):
    groups = ensure_role_groups()
    user.groups.remove(groups[JUROR_GROUP])
