#!/usr/bin/env python
"""
Create a test user with a verified email, bypassing allauth's email confirmation.

Usage:
    python create_test_artist.py --email EMAIL [--password PASSWORD]
                               [--artist] [--curator]
                               [--first FIRST] [--last LAST]
                               [--image IMAGE_PATH]

Defaults to test@example.com / testpass123 if not provided.

  --email EMAIL       User email address (default: test@example.com).
  --password PASSWORD User password (default: testpass123).
  --artist            Create a linked Artist profile (like normal signup).
  --curator           Create a linked Artist profile and set is_staff=True (curator access).
  --first FIRST       Set artist first name (requires --artist or --curator).
  --last LAST         Set artist last name (requires --artist or --curator).
  --image IMAGE_PATH  Optional path to an artist profile image (requires --artist or --curator).
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from allauth.account.models import EmailAddress

args = sys.argv[1:]

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

def _pop_flag(args, flag):
    if flag in args:
        args.remove(flag)
        return True
    return False

email      = _pop_flag_value(args, '--email') or 'test@example.com'
password   = _pop_flag_value(args, '--password') or 'testpass123'
first_name = _pop_flag_value(args, '--first')
last_name  = _pop_flag_value(args, '--last')
image_path = _pop_flag_value(args, '--image')
make_curator = _pop_flag(args, '--curator')
make_artist  = _pop_flag(args, '--artist') or make_curator

if image_path:
    image_path = os.path.expanduser(image_path)

User = get_user_model()

if User.objects.filter(email=email).exists():
    print(f'User {email} already exists.')
    sys.exit(1)

user = User.objects.create_user(username=email, email=email, password=password)
EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)

if make_artist:
    from accounts.signup import ensure_signup_profile
    from django.core.files import File
    artist, _ = ensure_signup_profile(user)
    if artist and (first_name or last_name):
        if first_name:
            artist.first_name = first_name
        if last_name:
            artist.last_name = last_name
        update = [f for f, v in (('first_name', first_name), ('last_name', last_name)) if v]
        artist.save(update_fields=update)
    if image_path and artist:
        if not os.path.exists(image_path):
            print(f'Warning: image not found: {image_path}')
        else:
            with open(image_path, 'rb') as f:
                artist.image.save(os.path.basename(image_path), File(f), save=True)
    artist_group, _ = Group.objects.get_or_create(name='artist')
    user.groups.add(artist_group)

if make_curator:
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    curator_group, _ = Group.objects.get_or_create(name='curator')
    user.groups.add(curator_group)

role = 'curator' if make_curator else ('artist' if make_artist else 'user')
print(f'Created {role}: {email} / {password}')
