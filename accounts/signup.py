from accounts.roles import add_artist_role
from gallery.models import Artist


def apply_google_profile_data(user, extra_data):
    changed_fields = []

    first_name = (extra_data.get('given_name') or user.first_name or '').strip()
    last_name = (extra_data.get('family_name') or user.last_name or '').strip()
    email = (extra_data.get('email') or user.email or '').strip()
    username = (user.username or email).strip()

    if user.first_name != first_name:
        user.first_name = first_name
        changed_fields.append('first_name')

    if user.last_name != last_name:
        user.last_name = last_name
        changed_fields.append('last_name')

    if email and user.email != email:
        user.email = email
        changed_fields.append('email')

    if username and user.username != username:
        user.username = username
        changed_fields.append('username')

    return changed_fields


def ensure_signup_profile(user):
    add_artist_role(user)

    full_name = ' '.join(part for part in [user.first_name, user.last_name] if part).strip() or user.email or user.username
    defaults = {
        'name': full_name,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'phone': '',
    }
    artist, created = Artist.objects.get_or_create(user=user, defaults=defaults)

    if created:
        return artist

    changed_fields = []
    field_values = {
        'name': full_name,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
    }
    for field_name, value in field_values.items():
        if getattr(artist, field_name) != value:
            setattr(artist, field_name, value)
            changed_fields.append(field_name)

    if changed_fields:
        artist.save(update_fields=changed_fields)

    return artist