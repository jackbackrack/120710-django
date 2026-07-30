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

# Tests must not depend on live reCAPTCHA (keys may be present in the dev env);
# disable it so captcha fields become no-ops and forms can be submitted in tests.
RECAPTCHA_ENABLED = False

# Quieter, faster: no debug overhead. (Caching is disabled during test runs by the
# base settings — 'test' in sys.argv → DummyCache — so cached fragments don't leak
# between tests.)
DEBUG = False

# Send campaigns inline. A background thread gets its own database connection, which cannot
# see the transaction a TestCase runs in, so a threaded send in tests would either find no
# subscribers or race the assertions.
CAMPAIGN_SEND_IN_BACKGROUND = False

# No throttling in tests. The real setting sleeps between messages to stay under the
# provider's rate limit, which would add half a second per message to the send tests.
CAMPAIGN_MESSAGES_PER_SECOND = 0
