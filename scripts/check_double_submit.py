#!/usr/bin/env python
"""Prove the single-submit guard actually stops a second click making a second thing.

    ./env/bin/python scripts/check_double_submit.py

Not part of the test suite. The suite can assert the script is *on the page*, which is not
the same as it working — that needs a real browser, and putting Playwright in the default run
would make it slow and give it a dependency it does not otherwise have. So this is a check
you run when the guard changes, in the same spirit as scripts/check.sh.

It drives a real browser against a real server: fills the artwork form, clicks Save twice as
fast as the browser will allow, and counts what was created. Run with the guard removed and
it reports two.

Also reports what the guard cannot do — a back-button resubmit is a fresh page load with no
memory of the first, so JavaScript cannot see it. That case needs a server-side idempotency
token, and this says so rather than leaving it to be discovered.
"""
import os
import sys
import time

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings_test')
django.setup()

from django.contrib.staticfiles.handlers import StaticFilesHandler  # noqa: E402
from django.test.runner import DiscoverRunner  # noqa: E402
from django.test.testcases import LiveServerThread  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

PASSWORD = 'guard-check'


class SlowPostHandler(StaticFilesHandler):
    """Holds every POST for a few seconds before handling it.

    The situation being reproduced is a slow request, and the browser is the wrong place to
    create one: Chrome's network emulation does not apply to loopback, so throttling the
    connection changed nothing and the page navigated before a second click could land.
    Delaying inside a Playwright route handler is worse — it blocks Playwright's own loop.

    Making the server slow leaves the page alive and interactive, which is the state a real
    person clicks in. LiveServerThread is threaded, so the second request is not queued
    behind the first.
    """

    def __call__(self, environ, start_response):
        if environ.get('REQUEST_METHOD') == 'POST':
            time.sleep(4)
        return super().__call__(environ, start_response)


def _db(fn, *args):
    """Run an ORM call from inside the running browser script.

    Playwright's sync API drives the browser from an event loop, so Django treats anything
    inside it as an async context and raises SynchronousOnlyOperation on any query. A worker
    thread gets its own context — the same escape hatch capture_howto uses for the same
    reason.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


def _count(title):
    from gallery.models import Artwork

    return Artwork.objects.filter(name=title).count()


def seed():
    """An artist with a login and nothing else, on a throwaway database."""
    import io
    import random

    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from gallery.models import Artist

    def photo():
        random.seed(4)
        img = Image.new('RGB', (60, 60))
        img.putdata([(random.randrange(256),) * 3 for _ in range(3600)])
        buf = io.BytesIO()
        img.save(buf, 'JPEG')
        buf.seek(0)
        return SimpleUploadedFile('p.jpg', buf.read(), 'image/jpeg')

    User = get_user_model()
    user = User.objects.create_user(username='guard@example.com',
                                    email='guard@example.com', password=PASSWORD)
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
    artist = Artist.objects.create(name='Guard Check', first_name='Guard',
                                   last_name='Check', email=user.email,
                                   zipcode='94710', user=user)
    artist.image.save('g.jpg', photo(), save=True)
    return user


def run(page, base_url, image_path):
    page.goto(f'{base_url}/accounts/login/')
    # The email and password fields sit behind a toggle, so nothing is visible until it is
    # clicked — which is why a bare submit selector found a hidden button and timed out.
    toggle = page.get_by_text('Log in with email and password')
    if toggle.count():
        toggle.first.click()
    page.fill('input[name="login"]', 'guard@example.com')
    page.fill('input[name="password"]', PASSWORD)
    page.eval_on_selector(
        'form:has(input[name="password"])',
        "form => form.querySelector('button[type=submit], input[type=submit]').click()")
    page.wait_for_load_state('networkidle')
    if '/accounts/login' in page.url:
        raise SystemExit('could not log in — the check cannot run')

    page.goto(f'{base_url}/artwork/new/')
    # Every required field, or the browser's own validation blocks submission before the
    # guard is ever reached — and a form that never submits looks exactly like a guard that
    # works. That is what the first run of this actually measured.
    # Attribute-only selectors: `medium` is a TextField and renders as a textarea, so
    # `input[name="medium"]` matches nothing and waits thirty seconds to say so.
    page.fill('[name="name"]', 'Double Click Test')
    page.fill('[name="end_year"]', '2026')
    page.fill('[name="medium"]', 'oil on panel')
    page.fill('[name="width_inches"]', '24')
    page.fill('[name="height_inches"]', '36')
    page.select_option('[name="pricing_type"]', 'nfs')
    # No artists control on this page: the view credits the logged-in user's own profile.
    page.set_input_files('[name="image"]', image_path)

    form = page.locator('form:has([name="name"])')
    if not form.evaluate('f => f.checkValidity()'):
        missing = form.evaluate(
            """f => Array.from(f.elements)
                     .filter(el => !el.checkValidity())
                     .map(el => el.name)""")
        raise SystemExit(f'the form is incomplete, so nothing would submit: {missing}')

    # Count what the browser actually sends, not what ends up in the database. Two clicks
    # can produce two POSTs of which only one is recorded, and one click can look like two;
    # the question the guard answers is how many requests leave the browser.
    posts = []
    page.on('request', lambda r: posts.append(r.url)
            if r.method == 'POST' and '/artwork/new' in r.url else None)

    # The server holds every POST for a few seconds — see SlowPostHandler. Two *synchronous*
    # clicks prove nothing, because the browser coalesces those by itself: the first version
    # of this check passed with the guard deliberately removed.
    click = """form => form.querySelector(
                   'button[type=submit], input[type=submit]').click()"""
    page.eval_on_selector('form:has([name="name"])', click)
    page.wait_for_timeout(1200)         # the first upload is now grinding away
    try:
        page.eval_on_selector('form:has([name="name"])', click)
    except Exception as exc:
        # The page navigated before the second click could land, so the upload was not slow
        # enough to reproduce anything and this run measured nothing. Reported with the real
        # error rather than a guess, because "too quick" is only one of the reasons.
        raise SystemExit(f'could not click a second time, so nothing was measured: {exc}')

    page.wait_for_timeout(12000)
    return len(posts)


def main():
    from playwright.sync_api import sync_playwright

    runner = DiscoverRunner(verbosity=0, interactive=False)
    setup_test_environment()
    old_config = runner.setup_databases()

    image_path = os.path.abspath('scripts/_guard_check.jpg')
    try:
        import io
        import random

        from PIL import Image
        # A realistic photograph, not a thumbnail: the whole point is a request that takes
        # long enough for somebody to click again, and noise compresses badly so the file
        # stays large.
        random.seed(7)
        side = 1600
        img = Image.new('RGB', (side, side))
        img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                     for _ in range(side * side)])
        img.save(image_path, 'JPEG', quality=95)
        print(f'  test photograph: {os.path.getsize(image_path) // 1024} KB')

        seed()

        server = LiveServerThread('localhost', SlowPostHandler, port=0)
        server.daemon = True
        server.start()
        server.is_ready.wait()
        if server.error:
            raise SystemExit(f'could not start the server: {server.error}')
        base_url = f'http://localhost:{server.port}'

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    posts = run(page, base_url, image_path)
                finally:
                    browser.close()
        finally:
            server.terminate()
    finally:
        runner.teardown_databases(old_config)
        if os.path.exists(image_path):
            os.remove(image_path)

    print()
    print(f'  a second click during a slow upload sent {posts} POST(s)')
    if posts == 1:
        print('  \033[32mPASS\033[0m  the second submit was blocked')
    else:
        print(f'  \033[31mFAIL\033[0m  expected 1, got {posts} — the guard is not working')
    print()
    print('  What this does not cover: a back-button resubmit is a fresh page load with no')
    print('  memory of the first, so no JavaScript can see it. Closing that needs a')
    print('  server-side idempotency token.')
    return 0 if posts == 1 else 1


if __name__ == '__main__':
    sys.exit(main())
