"""Find artist profile photos that are monograms rather than photographs.

    manage.py find_placeholder_photos
    manage.py find_placeholder_photos --show full-feel
    manage.py find_placeholder_photos --clear

Google hands out a `picture` for every account, and for one with no photo of its own that picture
is a monogram — an initial, white, on a strong flat colour. Those were imported as profile photos
until now, which filled the field, passed the form, and left the gallery chasing a real photo
after acceptance anyway. This finds the ones already in.

`--clear` blanks them, which puts those artists back in front of the requirement the next time
they edit their profile or submit. It does not email anybody; that is a decision, not a side
effect of a report.
"""
from django.core.management.base import BaseCommand

from gallery.models import Artist
from gallery.photos import looks_like_placeholder, photo_colours


class Command(BaseCommand):
    help = 'Report artist photos that look like placeholders rather than photographs.'

    def add_arguments(self, parser):
        parser.add_argument('--show', help='Only artists in this show (slug).')
        parser.add_argument('--clear', action='store_true',
                            help='Blank the offending photos, so the requirement applies again.')

    def handle(self, *args, **options):
        artists = Artist.objects.exclude(image='').exclude(image__isnull=True)
        if options['show']:
            artists = artists.filter(artworks__shows__slug=options['show']).distinct()

        checked = flagged = unreadable = 0
        found = []
        for artist in artists.order_by('last_name', 'first_name'):
            checked += 1
            try:
                with artist.image.open('rb') as handle:
                    measured = photo_colours(handle)
                    if measured is None:
                        unreadable += 1
                        continue
                    handle.seek(0)
                    placeholder = looks_like_placeholder(handle)
            except Exception as exc:   # noqa: BLE001 — a missing file is worth naming, not fatal
                self.stderr.write(f'  ! {artist.name}: {exc}')
                unreadable += 1
                continue

            if placeholder:
                flagged += 1
                found.append(artist)
                dominant, colours = measured
                self.stdout.write(
                    f'  {artist.name:<32} {artist.email or "no email":<32} '
                    f'{colours} colour(s), {dominant:.0%} one shade')

        self.stdout.write(f'\n{checked} photo(s) checked, {flagged} look like placeholders'
                          + (f', {unreadable} unreadable' if unreadable else ''))

        if not flagged:
            self.stdout.write(self.style.SUCCESS('Nothing to do.'))
            return

        if not options['clear']:
            self.stdout.write(self.style.WARNING(
                'Nothing changed. Re-run with --clear to blank these, which puts those artists '
                'back in front of the photo requirement.'))
            return

        for artist in found:
            artist.image.delete(save=False)
            artist.image = ''
            artist.save(update_fields=['image'])
        self.stdout.write(self.style.SUCCESS(
            f'Cleared {len(found)} photo(s). Those artists will be asked for one when they next '
            f'edit their profile or submit.'))
