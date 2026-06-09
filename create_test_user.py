#!/usr/bin/env python
"""
Create a test user with a verified email, bypassing allauth's email confirmation.

Usage:
    python create_test_user.py [email] [password] [--artist] [--curator]

Defaults to test@example.com / testpass123 if not provided.

  --artist   Create a linked Artist profile (like normal signup).
  --curator  Create a linked Artist profile and set is_staff=True (curator access).
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
flags = {a for a in args if a.startswith('--')}
positional = [a for a in args if not a.startswith('--')]

email = positional[0] if len(positional) > 0 else 'test@example.com'
password = positional[1] if len(positional) > 1 else 'testpass123'
make_artist = '--artist' in flags or '--curator' in flags
make_curator = '--curator' in flags

User = get_user_model()

if User.objects.filter(email=email).exists():
    print(f'User {email} already exists.')
    sys.exit(1)

user = User.objects.create_user(username=email, email=email, password=password)
EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)

if make_artist:
    from accounts.signup import ensure_signup_profile
    ensure_signup_profile(user)
    artist_group, _ = Group.objects.get_or_create(name='artist')
    user.groups.add(artist_group)

if make_curator:
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    curator_group, _ = Group.objects.get_or_create(name='curator')
    user.groups.add(curator_group)

role = 'curator' if make_curator else ('artist' if make_artist else 'user')
print(f'Created {role}: {email} / {password}')
