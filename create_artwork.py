#!/usr/bin/env python
"""
Create an artwork and assign it to the artist matching the given email.

Usage:
    python create_artwork.py <email> <name> <end_year> <width_inches> <height_inches> <image_path>

Example:
    python create_artwork.py artist@example.com "My Painting" 2025 12.5 18 ~/Downloads/painting.jpg
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')
django.setup()

from django.core.files import File
from gallery.models import Artist, Artwork

if len(sys.argv) < 7:
    print(__doc__)
    sys.exit(1)

email      = sys.argv[1]
name       = sys.argv[2]
end_year   = int(sys.argv[3])
width      = float(sys.argv[4])
height     = float(sys.argv[5])
image_path = os.path.expanduser(sys.argv[6])

artist = Artist.objects.filter(email__iexact=email).first()
if not artist:
    # Fall back to user email
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
    created_by=artist.user,
)

with open(image_path, 'rb') as f:
    artwork.image.save(os.path.basename(image_path), File(f), save=False)
    artwork.save()

artwork.artists.add(artist)

print(f'Created artwork "{artwork.name}" (pk={artwork.pk}) for artist {artist.name}.')
