from django.contrib.auth.models import Group


ARTIST_GROUP = 'artist'
CURATOR_GROUP = 'curator'
STAFF_GROUP = 'staff'


def ensure_role_groups():
    groups = {}
    for name in (ARTIST_GROUP, CURATOR_GROUP, STAFF_GROUP):
        groups[name], _ = Group.objects.get_or_create(name=name)
    return groups


def add_artist_role(user):
    groups = ensure_role_groups()
    user.groups.add(groups[ARTIST_GROUP])


def add_curator_role(user):
    groups = ensure_role_groups()
    user.groups.add(groups[ARTIST_GROUP], groups[CURATOR_GROUP])


def remove_curator_role(user):
    groups = ensure_role_groups()
    user.groups.remove(groups[CURATOR_GROUP])


def add_staff_role(user):
    groups = ensure_role_groups()
    user.groups.add(groups[STAFF_GROUP])
    if not user.is_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
