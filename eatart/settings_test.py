"""Fast settings for running the test suite.

Usage:
    python manage.py test --settings=eatart.settings_test --parallel auto --keepdb

- MD5 password hashing (Django's default hasher is deliberately slow; user-heavy
  tests spend most of their time hashing otherwise).
- In-memory email backend (explicit; the test runner already forces this).
"""
from eatart.settings import *  # noqa: F401,F403

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Quieter, faster: no debug overhead. (Caching is disabled during test runs by the
# base settings — 'test' in sys.argv → DummyCache — so cached fragments don't leak
# between tests.)
DEBUG = False
