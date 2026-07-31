import io
import logging
import re

from gallery.models import Artist


logger = logging.getLogger(__name__)


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


def import_google_avatar(artist, extra_data):
    """Save the Google account picture as the artist's profile photo.

    A profile photo is required before submitting, and Google already hands us one
    at signup — so for anyone who signs in with Google the requirement is met before
    they ever see it. Best-effort: signup must never fail because a photo fetch did.
    """
    if artist is None or artist.image:
        return False
    url = (extra_data or {}).get('picture')
    if not url:
        return False
    try:
        import requests
        from django.core.files.base import ContentFile
        # Google serves a small avatar by default; ask for one big enough to print.
        resp = requests.get(re.sub(r'=s\d+(-c)?$', '=s600-c', url), timeout=5)
        resp.raise_for_status()
        if not resp.headers.get('Content-Type', '').startswith('image/'):
            return False
        if len(resp.content) > 8 * 1024 * 1024:
            return False

        # A Google account with no picture of its own still has a `picture` claim, and it is a
        # monogram: one initial on a flat colour. Importing it fills the field, passes the form,
        # and leaves the gallery chasing a real photo after acceptance — the exact thing the
        # requirement exists to prevent, minus any warning that it is coming.
        from gallery.photos import looks_like_placeholder
        if looks_like_placeholder(io.BytesIO(resp.content)):
            logger.info('Google avatar for artist %s is a monogram; not importing', artist.pk)
            return False

        artist.image.save(f'google-{artist.pk}.jpg', ContentFile(resp.content), save=True)
        return True
    except Exception:   # noqa: BLE001 — a missing photo is recoverable; a failed signup is not
        logger.warning('Could not import Google avatar for artist %s', artist.pk, exc_info=True)
        return False


def ensure_signup_profile(user):
    """Create or claim an artist profile for a newly signed-up user.

    Returns (artist, status) where status is:
      'claimed'  — an existing unlinked record was found by email and linked
      'created'  — a brand-new artist record was created
      False      — an existing linked record was found; nothing changed
    Both 'claimed' and 'created' are truthy so callers can use `if status`.
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
            return unlinked, 'claimed'

    artist, created = Artist.objects.get_or_create(user=user, defaults=defaults)

    if created:
        return artist, 'created'

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