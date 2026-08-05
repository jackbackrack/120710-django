#!/usr/bin/env python
"""Seed a varied catalogue of artists and artworks from a JSON manifest.

Usage:
    python create_catalogue.py MANIFEST [--show SLUG] [--status STATUS] [--limit N]

Exists because the hand-written seed data reused four artwork images across fourteen
artworks and two artist photos across twelve artists (one artist's "photo" was a QR
code). Any screenshot of a card grid showed the same four paintings tiling, which reads
as a broken page rather than a gallery — and those grids appear in the published how-to
screenshots. See docs/visual-howto-documentation.md.

The manifest is `test_fixtures/full_feel_catalogue.json`, built from the real Feel-Full
show. **Each piece stays attached to the artist who made it** — the images are real work
by named artists, so the credit has to travel with them. Never reassign one of these to a
made-up artist; that is misattribution in a page we publish. Anything a capture script
uploads on behalf of its own throwaway artist is generated instead, in
`gallery/management/commands/capture_howto.py`.

Artists get no login: these are catalogue records, not accounts. The seed script's
`ready@`/`invited@`/etc. accounts are what the submission flows are exercised with.
"""
import argparse
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')
django.setup()

import json  # noqa: E402  (after django.setup)

from django.core.files import File  # noqa: E402

from gallery.models import Artist, Artwork, Show  # noqa: E402
from gallery.models.submissions import ArtworkSubmission  # noqa: E402


def _artist_for(entry):
    """Get or create the catalogue artist, keeping the work with its real maker."""
    name = entry['artist'].strip()
    first, _, last = name.partition(' ')
    artist = Artist.objects.filter(name=name).first()
    if artist is None:
        artist = Artist.objects.create(
            name=name, first_name=first, last_name=last or first,
            zipcode='94710',
            bio=f'{name} is one of the artists in the Feel-Full show.',
            statement='',
        )
    if entry.get('artist_image') and not artist.image:
        path = entry['artist_image']
        if os.path.exists(path):
            with open(path, 'rb') as fh:
                artist.image.save(os.path.basename(path), File(fh), save=True)
    return artist


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--show', default=None,
                        help='Show slug to attach the artworks to.')
    parser.add_argument('--status', default=None,
                        help='Submission status when --show is given (e.g. accepted).')
    parser.add_argument('--limit', type=int, default=None,
                        help='Only seed the first N entries.')
    args = parser.parse_args()

    with open(args.manifest) as fh:
        entries = json.load(fh)
    if args.limit:
        entries = entries[:args.limit]

    show = Show.objects.get(slug=args.show) if args.show else None

    created = 0
    attached = 0
    for entry in entries:
        artist = _artist_for(entry)
        existing = Artwork.objects.filter(name=entry['title'], artists=artist).first()
        if existing is not None:
            # Already seeded by an earlier call. Still honour --show, so the same
            # catalogue can be attached to more than one show: a piece needs to be in a
            # published or closed show to be *publicly* visible at all, while the jury
            # and curation guides need submissions on a show that is still in review.
            if show is not None:
                existing.shows.add(show)
                if args.status:
                    ArtworkSubmission.objects.get_or_create(
                        show=show, artwork=existing,
                        defaults={'status': args.status})
                attached += 1
            continue
        artwork = Artwork.objects.create(
            name=entry['title'],
            end_year=entry['year'],
            medium=entry['medium'],
            width_inches=entry['width'],
            height_inches=entry['height'],
            pricing_type=Artwork.PRICING_ON_REQUEST,
        )
        artwork.artists.add(artist)
        path = entry['artwork_image']
        if os.path.exists(path):
            with open(path, 'rb') as fh:
                artwork.image.save(os.path.basename(path), File(fh), save=True)
        if show is not None:
            artwork.shows.add(show)
            if args.status:
                ArtworkSubmission.objects.get_or_create(
                    show=show, artwork=artwork,
                    defaults={'status': args.status},
                )
        created += 1

    print(f'Catalogue: {created} new artworks by '
          f'{len({e["artist"] for e in entries})} artists'
          + (f', {attached} existing attached' if attached else '')
          + (f' → {show.name}' if show else ''))


if __name__ == '__main__':
    sys.exit(main())
