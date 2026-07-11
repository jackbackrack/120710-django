#!/usr/bin/env python
"""
Create a test site.

Usage:
    python create_test_site.py --name NAME [--address ADDRESS] [--email EMAIL]
                               [--phone PHONE] [--instagram HANDLE]
                               [--website URL] [--description TEXT]
                               [--image IMAGE_PATH] [--status STATUS]
                               [--lat LATITUDE] [--lng LONGITUDE]

  --name NAME           Site name (required).
  --street STREET       Street address.
  --city CITY           City.
  --state STATE         State / province / region.
  --postal-code CODE    Postal / zip code.
  --country COUNTRY     Country (default: USA).
  --email EMAIL         Contact email.
  --phone PHONE         Phone number.
  --instagram HANDLE    Instagram handle (with or without @).
  --website URL         Website URL.
  --description TEXT    Description text.
  --image IMAGE_PATH    Path to site hero image.
  --icon ICON_PATH      Path to site icon (small logo for nav/cards).
  --status STATUS       published or draft (default: published).
  --lat LATITUDE        Latitude for map pin (decimal degrees).
  --lng LONGITUDE       Longitude for map pin (decimal degrees).

Example:
    python create_test_site.py --name "120710" --address "1207 10th St, Berkeley CA" \\
                               --email info@120710.art --instagram @120710.art
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')
django.setup()

from django.core.files import File
from gallery.models import Site


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

name        = _pop_flag_value(args, '--name')
street      = _pop_flag_value(args, '--street') or ''
city        = _pop_flag_value(args, '--city') or ''
state       = _pop_flag_value(args, '--state') or ''
postal_code = _pop_flag_value(args, '--postal-code') or ''
country     = _pop_flag_value(args, '--country') or 'USA'
email       = _pop_flag_value(args, '--email') or ''
phone       = _pop_flag_value(args, '--phone') or ''
instagram   = _pop_flag_value(args, '--instagram') or ''
website     = _pop_flag_value(args, '--website') or ''
description = _pop_flag_value(args, '--description') or ''
image_path  = _pop_flag_value(args, '--image')
icon_path   = _pop_flag_value(args, '--icon')
status      = _pop_flag_value(args, '--status') or Site.STATUS_PUBLISHED
lat         = _pop_flag_value(args, '--lat')
lng         = _pop_flag_value(args, '--lng')

if not name:
    print('--name is required')
    print(__doc__)
    sys.exit(1)

valid_statuses = {s for s, _ in Site.STATUS_CHOICES}
if status not in valid_statuses:
    print(f'Invalid --status {status!r}. Choices: {", ".join(valid_statuses)}')
    sys.exit(1)

site = Site(
    name=name,
    street=street,
    city=city,
    state=state,
    postal_code=postal_code,
    country=country,
    email=email,
    phone=phone,
    instagram=instagram,
    website=website,
    description=description,
    status=status,
    latitude=float(lat) if lat else None,
    longitude=float(lng) if lng else None,
)

if image_path:
    image_path = os.path.expanduser(image_path)
    if not os.path.exists(image_path):
        print(f'Image file not found: {image_path}')
        sys.exit(1)
    with open(image_path, 'rb') as f:
        site.image.save(os.path.basename(image_path), File(f), save=False)

if icon_path:
    icon_path = os.path.expanduser(icon_path)
    if not os.path.exists(icon_path):
        print(f'Icon file not found: {icon_path}')
        sys.exit(1)
    with open(icon_path, 'rb') as f:
        site.icon.save(os.path.basename(icon_path), File(f), save=False)

site.save()

print(f'Created site "{site.name}" (pk={site.pk}, slug={site.slug}, status={site.status}).')
