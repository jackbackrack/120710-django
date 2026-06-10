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
    """Create or claim an artist profile for a newly signed-up user.

    Returns (artist, is_new) where is_new is True if the artist was just
    created or claimed from an unlinked record — i.e. the user should be
    sent to the edit page to complete their profile.
    """
    full_name = ' '.join(part for part in [user.first_name, user.last_name] if part).strip() or user.email or user.username
    defaults = {
        'name': full_name,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'phone': '',
    }

    # Check if an unlinked artist with matching email already exists and claim it.
    if user.email:
        unlinked = Artist.objects.filter(email__iexact=user.email, user__isnull=True).first()
        if unlinked:
            unlinked.user = user
            unlinked.save(update_fields=['user'])
            return unlinked, True

    artist, created = Artist.objects.get_or_create(user=user, defaults=defaults)

    if created:
        return artist, True

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

    _link_invitations(user, artist)
    return artist, False


def _link_invitations(user, artist):
    if not user.email or not artist:
        return
    from gallery.models import ShowInvitation
    ShowInvitation.objects.filter(email__iexact=user.email, artist__isnull=True).update(artist=artist)