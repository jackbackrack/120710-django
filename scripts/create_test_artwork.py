#!/usr/bin/env python
"""
Create an artwork and assign it to the artist matching the given email.

Usage:
    python create_test_artwork.py --email EMAIL --name NAME --year YEAR
                             --width WIDTH --height HEIGHT --image IMAGE_PATH
                             [--medium MEDIUM] [--show SHOW_SLUG]

  --medium MEDIUM     Medium / materials description.
  --show SHOW_SLUG    Submit the artwork to this show (by slug).

Example:
    python create_test_artwork.py --email artist@example.com --name "My Painting" \\
                             --year 2025 --width 12.5 --height 18 \\
                             --medium "Oil on canvas" --show feel-full \\
                             --image ~/Downloads/painting.jpg
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')
django.setup()

from django.core.files import File
from gallery.models import Artist, Artwork, ArtworkSubmission, Show

def _pop_flag_value(args, flag):
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            value = args[idx + 1]
            args.remove(flag)
            args.remove(value)
            return value
        args.remove(flag)
    return None

args = sys.argv[1:]
email      = _pop_flag_value(args, '--email')
name       = _pop_flag_value(args, '--name')
year_str   = _pop_flag_value(args, '--year')
width_str  = _pop_flag_value(args, '--width')
height_str = _pop_flag_value(args, '--height')
image_path = _pop_flag_value(args, '--image')
medium     = _pop_flag_value(args, '--medium') or ''
show_slug  = _pop_flag_value(args, '--show')

missing = [f for f, v in (('--email', email), ('--name', name), ('--year', year_str),
                           ('--width', width_str), ('--height', height_str), ('--image', image_path)) if not v]
if missing:
    print(f'Missing required arguments: {", ".join(missing)}')
    print(__doc__)
    sys.exit(1)

end_year   = int(year_str)
width      = float(width_str)
height     = float(height_str)
image_path = os.path.expanduser(image_path)

artist = Artist.objects.filter(email__iexact=email).first()
if not artist:
    artist = Artist.objects.filter(user__email__iexact=email).first()
if not artist:
    print(f'No artist found with email: {email}')
    sys.exit(1)

if not os.path.exists(image_path):
    print(f'Image file not found: {image_path}')
    sys.exit(1)

artwork = Artwork(
    name=name,
    end_year=end_year,
    width_inches=width,
    height_inches=height,
    medium=medium,
    created_by=artist.user,
)

with open(image_path, 'rb') as f:
    artwork.image.save(os.path.basename(image_path), File(f), save=False)
    artwork.save()

artwork.artists.add(artist)

if show_slug:
    show = Show.objects.filter(slug=show_slug).first()
    if not show:
        print(f'Show not found: {show_slug}')
        sys.exit(1)
    ArtworkSubmission.objects.get_or_create(
        show=show, artwork=artwork,
        defaults={'submitted_by': artist.user},
    )
    print(f'Created artwork "{artwork.name}" (pk={artwork.pk}) for artist {artist.name}, submitted to "{show.name}".')
else:
    print(f'Created artwork "{artwork.name}" (pk={artwork.pk}) for artist {artist.name}.')
