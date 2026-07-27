"""Create a ready-to-use test artist account, so the submission flow can be entered
at whatever step you are working on.

    python manage.py make_test_artist                      # complete profile
    python manage.py make_test_artist --state no-photo     # stops at the photo
    python manage.py make_test_artist --state new-signup   # as a fresh signup leaves you
    python manage.py make_test_artist --state no-account   # stops at signup
    python manage.py make_test_artist --reset              # delete and recreate

The email is verified for you, so nothing has to be fished out of the console. Local
development only — it refuses to run unless settings.LOCAL_DEV is on, because it sets
a known password and marks an address verified without ever sending mail.

`--state` picks how far along the artist already is, so you can jump straight to the
step under test instead of replaying signup every time:

    no-account  no user at all (just prints the address to sign up with)
    new-signup  exactly what real signup leaves: name and email set, zip and photo
                outstanding. (Artist.save() backfills first/last from `name`, and the
                signup form requires both, so a brand-new artist is never nameless.)
    no-photo    only the photo outstanding — the most common sticking point
    complete    ready to submit

Pair it with --show to print the exact URL to start from.
"""
import io

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from gallery.models import Artist, Show

STATES = ('no-account', 'new-signup', 'no-photo', 'complete')


class Command(BaseCommand):
    help = ('Create a verified test artist at a chosen point in the submission flow '
            '(local development only).')

    def add_arguments(self, parser):
        parser.add_argument('--email', default='test-artist@example.com',
                            help='Address to use (default test-artist@example.com).')
        parser.add_argument('--password', default='testpass123',
                            help='Password to set (default testpass123).')
        parser.add_argument('--state', choices=STATES, default='complete',
                            help='How far along the artist already is (default complete).')
        parser.add_argument('--show', default=None,
                            help='Show slug — prints the URL to start the flow from.')
        parser.add_argument('--reset', action='store_true',
                            help='Delete any existing account for this email first.')

    def handle(self, *args, **opts):
        if not getattr(settings, 'LOCAL_DEV', False):
            raise CommandError(
                'make_test_artist only runs in local development (settings.LOCAL_DEV). '
                'It sets a known password and marks an email verified without sending '
                'mail, which must never happen against a real deployment.')

        email = opts['email'].strip().lower()
        state = opts['state']
        User = get_user_model()

        if opts['reset']:
            n, _ = User.objects.filter(email__iexact=email).delete()
            Artist.objects.filter(email__iexact=email, user__isnull=True).delete()
            if n:
                self.stdout.write(f'Removed the existing account for {email}.')

        if state == 'no-account':
            if User.objects.filter(email__iexact=email).exists():
                raise CommandError(
                    f'{email} already has an account — pass --reset to clear it first.')
            self._report(email, None, None, state, opts)
            return

        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(
                f'{email} already exists. Pass --reset to recreate it, or --email to '
                f'use a different address.')

        with transaction.atomic():
            user = User.objects.create_user(
                username=email, email=email, password=opts['password'],
                first_name='Tess', last_name='Tester')
            self._verify(user)
            artist = Artist.objects.create(
                user=user, name='Tess Tester', email=email,
                first_name='Tess', last_name='Tester',
                zipcode='' if state == 'new-signup' else '94710',
            )
            if state == 'complete':
                artist.image.save(f'test-artist-{artist.pk}.jpg',
                                  ContentFile(_placeholder_jpeg()), save=True)

        self._report(email, user, artist, state, opts)

    def _verify(self, user):
        """Mark the address confirmed, so no console-fishing is needed."""
        try:
            from allauth.account.models import EmailAddress
        except ImportError:      # pragma: no cover — allauth is a hard dependency
            return
        EmailAddress.objects.update_or_create(
            user=user, email=user.email,
            defaults={'verified': True, 'primary': True})

    def _report(self, email, user, artist, state, opts):
        w = self.stdout.write
        w(self.style.SUCCESS(f'\nTest artist "{email}" — state: {state}'))
        if user is None:
            w('  No account created. Sign up with this address to test signup itself.')
        else:
            w(f'  password: {opts["password"]}   (email already verified)')
            w(f'  artist:   {artist} — photo: {"yes" if artist.image else "no"}, '
              f'zip: {artist.zipcode or "—"}')
        slug = opts['show']
        if slug:
            show = Show.objects.filter(slug=slug).first()
            if show is None:
                w(self.style.WARNING(f'  (no show with slug "{slug}")'))
            else:
                w(f'  start here: {show.get_absolute_url()}')
                w(f'  submit:     {reverse("gallery:artwork_submit", kwargs={"slug": show.slug})}')
        w('')


def _placeholder_jpeg():
    """A small solid-colour JPEG — enough to satisfy the photo requirement."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (600, 600), (122, 148, 130)).save(buf, 'JPEG', quality=80)
    return buf.getvalue()
