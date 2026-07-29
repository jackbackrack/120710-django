"""Capture the per-step screenshots for a how-to guide by actually walking the flow.

    python manage.py capture_howto submit-artwork-public
    python manage.py capture_howto --list
    python manage.py capture_howto submit-artwork-public --keep

Screenshots are regenerated, never maintained. Run this after any UI change that
touches a captured flow; it drives a real browser through the steps the guide
describes and overwrites `static/img/howto/<image key>/NN.webp`. See
`docs/visual-howto-documentation.md` for the reasoning and `eatart/howto_images.py`
for how the help page picks the images up.

**A failed run is a documentation bug, not a broken screenshot.** The scripts locate
controls by the visible text the guide tells the reader to look for ("choose Sign Up",
"click New", "click Retract"). So when a button is reworded and this command reports a
DocumentationMismatch, the prose in `eatart/role_docs.py` is now wrong too — fix the
words, then re-run. That coupling is the point: it is the only mechanism that keeps the
written guides honest about button names.

Local development only. It signs up an account, uploads files, and submits work; and
curator-visible pages carry artist emails and phone numbers, so this must never be
pointed at a deployment.

The database is written to. Each script resets its own throwaway account first, so
runs are repeatable, and cleans up afterwards unless `--keep` is passed. Seed the rest
of the world with `scripts/create_test_database.sh` — show dates there are relative to
the day it runs, so a capture in July and one in December produce the same pages.
"""
import hashlib
import io
import pathlib
import shutil
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management.base import BaseCommand, CommandError
from django.test.testcases import LiveServerThread
from django.utils.text import slugify

from eatart.howto_images import (HOWTO_CAPTURE_SCALE, image_key, load_manifest,
                                 save_manifest, staging_dir, step_filename)
from eatart.role_docs import HOW_TO_GUIDES
from gallery.models import Artist, Artwork, Show
from gallery.models.submissions import ArtworkSubmission
from reviews.models import ArtworkReview

# Long enough to cover a cold first page load and an image upload, short enough that a
# genuinely missing control fails the run instead of hanging it.
DEFAULT_TIMEOUT_MS = 10_000


class DocumentationMismatch(CommandError):
    """A control the guide names could not be found — the prose is out of date."""


class Recorder:
    """Drives the browser and writes the numbered PNGs for one guide.

    Every lookup goes through `click`/`fill`/`expect`, which turn a Playwright timeout
    into a DocumentationMismatch naming the step and the words that no longer match.
    """

    def __init__(self, page, base_url, out_dir, write, timeout=DEFAULT_TIMEOUT_MS):
        self.page = page
        self.base_url = base_url
        self.out_dir = out_dir
        self.write = write
        self.timeout = timeout
        self.step = None
        self.captured = set()

    # -- navigation -------------------------------------------------------------

    def goto(self, path):
        self.page.goto(f'{self.base_url}{path}', wait_until='load')

    def sign_out(self):
        """Drop the session, for guides that photograph two people's views.

        Clears cookies rather than driving allauth's logout, which is a POST behind a
        confirmation page — this is about changing who the browser is, not about
        documenting how to sign out.
        """
        self.page.context.clear_cookies()

    def at_step(self, number):
        """Mark which guide step the following actions illustrate."""
        self.step = number

    # -- locating ---------------------------------------------------------------

    def control(self, name, exact=True):
        """The link *or* button labelled `name`.

        Guides say "choose Sign Up" and "click New" — they describe the words, not the
        markup, and the reader cannot tell an `<a>` from a `role="button"` anyway. The
        nav's Account menu is a link element carrying `role="button"`, so pinning either
        role here would make the script break on a purely cosmetic markup change while
        the guide's wording stayed perfectly correct.
        """
        return (self.page.get_by_role('button', name=name, exact=exact)
                .or_(self.page.get_by_role('link', name=name, exact=exact)))

    def form_with(self, field):
        """The form containing `field` — as a CSS selector string, for shot()/submit().

        Pages carry more than one form (the navbar's, allauth's social-login form), and
        several are hidden, so "the first form" is neither the right one nor reliably
        screenshottable. Identify it by a field only it has.
        """
        return f'form:has({field})'

    def submit(self, phrasing, form):
        """Submit `form` (a selector from form_with) via its own submit control.

        A rejected Django form re-renders at the same URL with a 200, so without this
        check the run sails on and fails several steps later with a confusing message
        about the *next* page. Surfacing the validation errors here names the real cause.
        """
        # Native constraint validation blocks submission before any JS or Django sees
        # it, leaving the page completely unchanged and the message in a browser tooltip
        # that is invisible to the DOM. Asking the form directly is the only way to see
        # it — and a required field the guide never mentions is a documentation bug, so
        # this needs to say so rather than look like a hang.
        blocked = self.page.evaluate(
            '''(sel) => {
                 const f = document.querySelector(sel);
                 if (!f) return null;
                 return [...f.elements].filter(e => !e.checkValidity())
                        .map(e => `${e.name || e.id}: ${e.validationMessage}`);
               }''', form)
        if blocked:
            raise DocumentationMismatch(
                f'step {self.step}: the browser refuses to submit this form — fields '
                f'the guide does not tell the reader to fill are required:\n'
                + '\n'.join(f'        {b}' for b in blocked)
                + '\n    A reader following the guide literally would be stuck here, '
                  'with only a native tooltip to explain why.')

        before = self.page.url
        self.click(phrasing, self.page.locator(
            f'{form} button[type="submit"], {form} input[type="submit"]'))
        self.page.wait_for_load_state('load')
        # Crispy marks the offending widgets, which names the fields; the message text
        # alone is often just a bare "This field is required."
        fields = self.page.locator('.is-invalid, [aria-invalid="true"]').evaluate_all(
            'els => els.map(e => e.getAttribute("name")).filter(Boolean)')
        messages = [t.strip().replace('\n', ' ') for t in self.page.locator(
            '.errorlist, .invalid-feedback, .alert-danger'
        ).all_inner_texts() if t.strip()]
        if not fields and not messages:
            # No evidence of rejection. Landing back on the same URL is not evidence
            # either: several views post-redirect-get to themselves, and treating that as
            # a failure made a working invitation form look broken.
            return
        detail = []
        if fields:
            detail.append(f'rejected fields: {sorted(set(fields))}')
        detail.extend(messages)
        raise CommandError(
            f'step {self.step}: the form on {before} was rejected.\n'
            + '    ' + '\n    '.join(detail))

    # -- interaction ------------------------------------------------------------

    def _mismatch(self, phrasing, exc):
        raise DocumentationMismatch(
            f'step {self.step}: the guide tells the reader to {phrasing}, but that is '
            f'not on {self.page.url}.\n'
            f'    Either the UI changed (reword the step in eatart/role_docs.py, it is '
            f'now wrong for readers too),\n'
            f'    or the flow moved (update the script in this file).'
        ) from exc

    def click(self, phrasing, locator):
        from playwright.sync_api import Error as PlaywrightError
        try:
            locator.first.click(timeout=self.timeout)
        except PlaywrightError as exc:
            self._mismatch(phrasing, exc)

    def fill(self, phrasing, selector, value):
        from playwright.sync_api import Error as PlaywrightError
        try:
            self.page.fill(selector, value, timeout=self.timeout)
        except PlaywrightError as exc:
            self._mismatch(phrasing, exc)

    def select(self, phrasing, selector, value, navigates=False):
        """Choose an option. Set `navigates` when the select submits its form on change.

        Some pickers reload the page from an onchange handler; without waiting for that
        the next call runs mid-navigation and Playwright reports "Execution context was
        destroyed", which says nothing about which control caused it.
        """
        from playwright.sync_api import Error as PlaywrightError
        try:
            if navigates:
                with self.page.expect_navigation():
                    self.page.select_option(selector, value, timeout=self.timeout)
            else:
                self.page.select_option(selector, value, timeout=self.timeout)
        except PlaywrightError as exc:
            self._mismatch(phrasing, exc)

    def set_file(self, phrasing, selector, name, content):
        from playwright.sync_api import Error as PlaywrightError
        try:
            self.page.set_input_files(
                selector,
                files=[{'name': name, 'mimeType': 'image/jpeg', 'buffer': content}],
                timeout=self.timeout,
            )
        except PlaywrightError as exc:
            self._mismatch(phrasing, exc)

    def expect_visible(self, phrasing, selector):
        """Wait for `selector` to be visible, as a documentation-shaped assertion.

        For controls that appear only once JS has run — the card-size widget is inserted
        hidden and revealed when a card grid is present. Going through here rather than
        calling `wait_for` directly is what turns a timeout into a message naming the step.
        """
        from playwright.sync_api import Error as PlaywrightError
        try:
            self.page.locator(selector).first.wait_for(
                state='visible', timeout=self.timeout)
        except PlaywrightError as exc:
            self._mismatch(phrasing, exc)

    def expect_text(self, phrasing, text):
        """Assert the reader would see `text` — used for the outcomes steps promise."""
        from playwright.sync_api import Error as PlaywrightError
        try:
            self.page.get_by_text(text, exact=False).first.wait_for(
                state='visible', timeout=self.timeout)
        except PlaywrightError as exc:
            self._mismatch(phrasing, exc)

    # -- capture ----------------------------------------------------------------

    def _settle(self):
        """Make a shot reproducible before taking it.

        Two runs of the same script produced different bytes for exactly the steps that
        ended with focus in a text field: a focused input draws a focus ring and a caret
        that blinks on a timer. That made every `--all` republish everything and leave the
        previous objects orphaned in the bucket — and a caret sitting mid-field also reads
        as a typo in documentation. Dropping focus fixes both.

        Also waits for in-flight image loads, so an uploaded preview is either there or
        not rather than half-arrived.
        """
        from playwright.sync_api import Error as PlaywrightError

        self.page.evaluate(
            '() => { if (document.activeElement) document.activeElement.blur(); }')
        try:
            self.page.wait_for_load_state('networkidle', timeout=3_000)
        except PlaywrightError:
            # networkidle legitimately never settles on some pages; a shot taken anyway
            # beats failing the run.
            pass
        # Long enough for the 0.1s card outline transition to finish.
        self.page.wait_for_timeout(200)

    def shot(self, number, selector=None):
        """Write NN.webp for step `number`.

        `selector` crops to one element, and you almost always want it. The help page
        renders these at 1:1, so extent is legibility: a whole-viewport shot of a 2000px
        form either overflows the column or gets shrunk until the labels are mush. Crop
        to the region the step is actually about.
        """
        self.at_step(number)
        self._settle()
        if selector:
            from playwright.sync_api import Error as PlaywrightError
            try:
                target = self.page.locator(selector).first
                target.scroll_into_view_if_needed(timeout=self.timeout)
                raw = target.screenshot()
            except PlaywrightError as exc:
                self._mismatch(f'see the region matched by "{selector}"', exc)
        else:
            raw = self.page.screenshot()
        self._write(number, raw)

    def shot_region(self, number, *selectors):
        """Write NN.webp cropped to the union of `selectors`' bounding boxes.

        For steps whose subject spans several elements but not the whole page — the two
        fieldsets of a long form, a heading plus the button beneath it. Playwright can
        only screenshot one element, so the box is computed here and passed as a clip.

        Selectors are resolved by Playwright, not `document.querySelector`, so its text
        engine is available: `.section-label:has-text("Artworks")` picks the right one of
        several identical sections. Plain CSS could only take the first match, which
        silently cropped the wrong section of the Me page.
        """
        self.at_step(number)
        self._settle()
        boxes = []
        for selector in selectors:
            locator = self.page.locator(selector).first
            if not locator.count():
                continue
            box = locator.bounding_box()
            if box and box['width'] and box['height']:
                boxes.append(box)
        if not boxes:
            present = [sel for sel in selectors if self.page.locator(sel).first.count()]
            hidden = (f' {present} exist but are not rendered (hidden, zero-size, or '
                      f'behind a menu that has not been opened).' if present else '')
            raise DocumentationMismatch(
                f'step {self.step}: nothing to clip from {list(selectors)} on '
                f'{self.page.url}.{hidden}\n'
                f'    The page changed shape — check what this step is describing.')

        # bounding_box() is viewport-relative; the clip is document-relative. Nothing
        # scrolls between the measurements above, so one offset applies to all of them.
        scroll_x, scroll_y = self.page.evaluate('[window.scrollX, window.scrollY]')
        left = min(b['x'] for b in boxes) + scroll_x
        top = min(b['y'] for b in boxes) + scroll_y
        right = max(b['x'] + b['width'] for b in boxes) + scroll_x
        bottom = max(b['y'] + b['height'] for b in boxes) + scroll_y
        raw = self.page.screenshot(full_page=True, clip={
            'x': max(0, left), 'y': max(0, top),
            'width': right - max(0, left), 'height': bottom - max(0, top),
        })
        self._write(number, raw)

    def shot_pdf(self, number, path, page_number=1, css_width=760):
        """Render one page of a PDF the app generates, and write it as this step's image.

        Several guides are almost entirely about what a generated PDF *contains* — the
        checklist's cover and artist pages, the layout of an Avery card sheet. Screenshotting
        the button that produces them illustrates one step out of six; rendering the page
        shows the thing the guide is describing.

        Fetched through the browser's own request context so the session cookie goes with
        it — these endpoints are curator-only. Rasterised with pdftoppm (poppler), at
        whatever resolution gives `css_width` after the 2x capture scale is divided out.
        """
        import shutil as _shutil
        import subprocess
        import tempfile

        self.at_step(number)
        if _shutil.which('pdftoppm') is None:
            raise CommandError(
                'pdftoppm (poppler) is not installed, so PDF pages cannot be rendered.\n'
                '    brew install poppler')

        response = self.page.context.request.get(f'{self.base_url}{path}')
        if not response.ok:
            raise DocumentationMismatch(
                f'step {self.step}: {path} returned HTTP {response.status}, so there is '
                f'no PDF to show.\n'
                f'    Either the link moved or this reader may not generate it.')
        body = response.body()
        if not body.startswith(b'%PDF'):
            raise DocumentationMismatch(
                f'step {self.step}: {path} did not return a PDF (got '
                f'{body[:16]!r}). The guide says this link downloads one.')

        # US Letter is 8.5in wide; render at 2x the target CSS width so the result matches
        # what a browser screenshot at HOWTO_CAPTURE_SCALE would have produced.
        dpi = max(72, round(css_width * HOWTO_CAPTURE_SCALE / 8.5))
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'in.pdf'
            src.write_bytes(body)
            if page_number == 'last':
                # Sections that come last — the checklist's artist bios — are addressed by
                # position, not by a number that changes with the size of the show.
                info = subprocess.run(['pdfinfo', str(src)],
                                      check=True, capture_output=True, text=True).stdout
                pages = [ln.split(':', 1)[1].strip() for ln in info.splitlines()
                         if ln.startswith('Pages:')]
                if not pages:
                    raise CommandError(
                        f'step {self.step}: pdfinfo did not report a page count for '
                        f'{path}.')
                page_number = int(pages[0])
            subprocess.run(
                ['pdftoppm', '-png', '-r', str(dpi),
                 '-f', str(page_number), '-l', str(page_number),
                 str(src), str(pathlib.Path(tmp) / 'page')],
                check=True, capture_output=True)
            rendered = sorted(pathlib.Path(tmp).glob('page*.png'))
            if not rendered:
                raise CommandError(
                    f'step {self.step}: {path} has no page {page_number}.')
            raw = rendered[0].read_bytes()
        self._write(number, raw)

    def _write(self, number, raw):
        """Re-encode a raw PNG screenshot as WebP and record it.

        Playwright only emits PNG, and a full-resolution one of a form runs to several
        megabytes — far too much to commit ~60 of. WebP holds the same pixels at roughly
        a tenth of that, so nothing is downscaled and legibility is untouched.
        """
        from PIL import Image

        path = self.out_dir / step_filename(number)
        image = Image.open(io.BytesIO(raw)).convert('RGB')
        image.save(path, 'WEBP', quality=90, method=6)
        self.captured.add(number)
        self.write(f'  step {number:>2} → {path.name}  '
                   f'{image.width // HOWTO_CAPTURE_SCALE}×'
                   f'{image.height // HOWTO_CAPTURE_SCALE} css px, '
                   f'{path.stat().st_size // 1024} KB')


# ---------------------------------------------------------------------------
# Capture scripts. One per image key; see CAPTURE_SCRIPTS at the bottom.
# ---------------------------------------------------------------------------

CAPTURE_EMAIL = 'howto-capture@example.com'
CAPTURE_PASSWORD = 'Howto-Capture-9273'
CAPTURE_ARTWORK_TITLE = 'Study in Green'


def _open_call_show():
    """A show a brand-new artist can actually submit to.

    Looked up rather than hard-coded: the seed script's slug is one rename away from
    breaking the run, and "is accepting submissions" is the property that matters.
    """
    for show in Show.objects.filter(status=Show.STATUS_OPEN_CALL).order_by('pk'):
        if show.is_accepting_submissions and not show.invitations.exists():
            return show
    raise CommandError(
        'No open-call show is accepting submissions, so the submission flow cannot be '
        'walked. Re-seed with `bash scripts/create_test_database.sh` (it backs the '
        'dates off today, so a stale database is the usual cause).')


def _reset_capture_account():
    """Delete the throwaway account and everything it made.

    Artworks first: they are only reachable through the artist, so deleting the account
    would strand them as ownerless rows that then clutter every artwork listing. A run
    that dies midway leaves the same debris, which is why this runs before each capture
    as well as after.
    """
    User = get_user_model()
    users = User.objects.filter(email__iexact=CAPTURE_EMAIL)
    Artwork.objects.filter(created_by__in=users).delete()
    Artwork.objects.filter(name=CAPTURE_ARTWORK_TITLE, artists__isnull=True).delete()
    users.delete()
    Artist.objects.filter(email__iexact=CAPTURE_EMAIL).delete()


def _db(fn, *args):
    """Run an ORM call from inside a running capture script.

    Playwright's sync API drives the browser from an event loop, so Django treats
    everything inside it as an async context and raises SynchronousOnlyOperation on any
    query. A worker thread gets its own context — the escape hatch that error message
    recommends.

    Used for two things: facts that do not exist until the browser has done something
    (an email-confirmation key), and each guide's reset/prepare/cleanup during a batch
    run, which happens inside the loop because one browser serves the whole batch.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


def _email_confirmation_path(email):
    """The path behind the "confirm your email address" link we just emailed.

    Real readers click a link in their inbox. Reconstructing the same URL from the token
    is the closest a headless run can get without parsing console output, and it still
    exercises the real confirmation view.
    """
    from allauth.account.models import EmailAddress, EmailConfirmationHMAC

    address = (EmailAddress.objects.filter(email__iexact=email)
               .order_by('-pk').first())
    if address is None:
        raise CommandError(
            f'Signup appeared to succeed but left no email address record for {email}, '
            f'so there is no confirmation link to follow.')
    return f'/accounts/confirm-email/{EmailConfirmationHMAC(address).key}/'


# ── Shared flow fragments ────────────────────────────────────────────────────
# Several guides describe the same screens from different angles: signup appears in both
# the sign-up guide and the submission guide, the artwork form in both the add-artworks
# and submission guides. These perform the actions and hand back the form selector, so
# each script decides for itself which steps to photograph and how to crop them — the
# framing is what differs between guides, not the driving.


def _open_signup(rec):
    """Account menu → Sign Up. Returns the signup form's selector."""
    rec.goto('/')
    rec.click('open the Account menu', rec.control('Account'))
    rec.click('choose Sign Up', rec.control('Sign Up'))
    return rec.form_with('input[name="password1"]')


def _fill_signup(rec, email=None, password=CAPTURE_PASSWORD):
    """Fill the signup form. Returns its selector, unsubmitted, ready to photograph."""
    email = email or CAPTURE_EMAIL
    rec.fill('enter your first name', 'input[name="first_name"]', 'Tess')
    rec.fill('enter your last name', 'input[name="last_name"]', 'Tester')
    rec.fill('enter your email address', 'input[name="email"]', email)
    rec.fill('enter your password', 'input[name="password1"]', password)
    # Only present when ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE is on, so not assumed.
    if rec.page.locator('input[name="password2"]').count():
        rec.fill('confirm your password', 'input[name="password2"]', password)
    return rec.form_with('input[name="password1"]')


def _open_email_confirmation(rec, email=None):
    """Follow the emailed confirmation link. Returns the confirm form's selector.

    The link does not exist until signup has happened, so this is the one lookup resolved
    mid-run rather than in `prepare` — see _db().
    """
    rec.goto(_db(_email_confirmation_path, email or CAPTURE_EMAIL))
    return 'form[action*="confirm-email"]'


def _fill_login(rec, email=None, password=CAPTURE_PASSWORD):
    """Reveal and fill the log-in form. Returns its selector, unsubmitted."""
    # The email/password fields sit behind a toggle, so the reader has to reveal them.
    toggle = rec.page.get_by_text('Log in with email and password')
    if toggle.count():
        rec.click('choose to log in with email and password', toggle)
    rec.fill('enter your email address', 'input[name="login"]', email or CAPTURE_EMAIL)
    rec.fill('enter your password', 'input[name="password"]', password)
    return rec.form_with('input[name="password"]')


def _log_in(rec, email=None, password=CAPTURE_PASSWORD):
    """Go to the log-in page and sign in. For scripts whose guide starts signed in."""
    rec.goto('/accounts/login/')
    rec.submit('log in', _fill_login(rec, email, password))


def _fill_profile(rec, zipcode='94710'):
    """Fill the artist profile's required fields. Returns the form's selector."""
    form = rec.form_with('input[name="zipcode"]')
    rec.fill('fill in your zip code', f'{form} input[name="zipcode"]', zipcode)
    rec.set_file('upload a profile photo', f'{form} input[name="image"]',
                 'profile.jpg', _portrait_placeholder())
    return form


def _fill_artwork(rec, title=CAPTURE_ARTWORK_TITLE, pricing='on_request'):
    """Fill the new-artwork form. Returns its selector.

    Fields are matched by name without a tag: `medium` is a TextField and so renders as a
    <textarea>, and which widget a field gets is not something the guide's prose depends
    on. `end_year` identifies the form because "name" and "image" are common enough to
    appear on other forms on the page.
    """
    form = rec.form_with('[name="end_year"]')
    rec.fill('fill in the title', f'{form} [name="name"]', title)
    rec.fill('fill in the year', f'{form} [name="end_year"]', '2026')
    rec.fill('fill in the medium', f'{form} [name="medium"]', 'Oil on canvas')
    rec.fill('fill in the width', f'{form} [name="width_inches"]', '24')
    rec.fill('fill in the height', f'{form} [name="height_inches"]', '36')
    rec.set_file('upload an image of the artwork itself', f'{form} [name="image"]',
                 'artwork.jpg', _artwork_placeholder())
    # Required, and the model's default never reaches the form as an initial value, so
    # the select loads on its empty placeholder. The guides now say so.
    rec.select('choose a Pricing option', f'{form} [name="pricing_type"]', pricing)
    return form


def _create_verified_artist(email, password=CAPTURE_PASSWORD, complete=True):
    """Make a ready-to-use account without walking signup.

    Only the sign-up guide documents signup itself; every other guide starts from "sign
    in". Creating the account directly keeps those scripts off the signup form entirely,
    which means they need neither the reCAPTCHA workaround nor the email-confirmation
    round trip — and keeps them from mutating the seeded accounts, whose state other
    guides depend on.

    Mirrors `manage.py make_test_artist`; called from `prepare`, so ORM access is fine.
    """
    from django.core.files.base import ContentFile

    User = get_user_model()
    user = User.objects.create_user(username=email, email=email, password=password,
                                    first_name='Tess', last_name='Tester')
    try:
        from allauth.account.models import EmailAddress
        EmailAddress.objects.update_or_create(
            user=user, email=email, defaults={'verified': True, 'primary': True})
    except ImportError:      # pragma: no cover — allauth is a hard dependency
        pass
    artist = Artist.objects.create(
        user=user, name='Tess Tester', email=email,
        first_name='Tess', last_name='Tester',
        zipcode='94710' if complete else '')
    if complete:
        artist.image.save(f'howto-artist-{artist.pk}.jpg',
                          ContentFile(_portrait_placeholder()), save=True)
    return artist


def prepare_submit_artwork():
    """Resolve everything the run needs from the database, before the browser starts.

    Playwright's sync API drives the browser from an event loop, which makes the whole
    script an async context as far as Django is concerned — any ORM call inside it
    raises SynchronousOnlyOperation. So DB lookups happen here and reach the script as
    plain values.
    """
    show = _open_call_show()
    return {'show_slug': show.slug, 'show_name': show.name}


def capture_submit_artwork(rec, facts):
    """The submission guide, end to end: signup → confirm → log in → profile → work →
    submit → retract.

    One guide serves signed-out and signed-in readers, so the script walks the whole arc
    including account creation. Steps with no single screen are declared in `prose_only`
    rather than silently skipped.
    """
    # Step 2 — "open the Account menu ... choose Sign Up. Enter your first name, last
    #           name, email address, and password".
    rec.at_step(2)
    _open_signup(rec)
    signup_form = _fill_signup(rec)
    rec.shot(2, selector=signup_form)
    rec.submit('submit the signup form', signup_form)

    # Step 3 — "signing up does not sign you in ... open it and click the Confirm button."
    rec.at_step(3)
    rec.expect_text('land on a "Verify Your Email Address" page',
                    'Verify Your Email Address')
    confirm_form = _open_email_confirmation(rec)
    rec.shot_region(3, 'h1', confirm_form)
    rec.submit('click the Confirm button', confirm_form)

    # Step 4 — "confirming your address takes you to the log-in page. Sign in with the
    #           email address and password you just chose."
    rec.at_step(4)
    if '/login' not in rec.page.url:
        raise DocumentationMismatch(
            f'step 4: the guide says confirming takes the reader to the log-in page, '
            f'but it went to {rec.page.url}.\n'
            f'    If signing in now happens automatically (allauth\'s '
            f'ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION), this step should be deleted from '
            f'the guide rather than reworded.')
    login_form = _fill_login(rec)
    # Includes the "You have confirmed …" message: it is the reader's confirmation that
    # step 3 worked, and the reason they are looking at a log-in page at all.
    rec.shot_region(4, '.alert', login_form)
    rec.submit('log in', login_form)

    # Step 5 — "logging in for the first time takes you to your artist profile edit page.
    #           Fill in your first name, last name, and zip code. Then upload a profile
    #           photo ... Click Update to save."
    rec.at_step(5)
    if '/edit/' not in rec.page.url:
        raise DocumentationMismatch(
            f'step 5: the guide promises a first log-in lands the reader on their artist '
            f'profile edit page, but it landed on {rec.page.url}.\n'
            f'    Either the post-login redirect changed, or the step is now wrong.')
    profile_form = _fill_profile(rec)
    # Just the "Required" fieldset — the optional bio/statement/socials below it run the
    # form past 2000px and are not what this step is about.
    rec.shot_region(5, 'fieldset:has(#div_id_zipcode)')
    rec.submit('click Update to save', profile_form)

    # Step 6 — "go to your Me page, scroll down to the Artworks section, and click New.
    #           Fill in the title, year, medium, and dimensions, and upload an image".
    rec.at_step(6)
    rec.click('go to your Me page', rec.control('Me'))
    rec.click('click New under Artworks', rec.control('New'))
    artwork_form = _fill_artwork(rec)
    # "Required" plus "Pricing" — every field this step names, and none of the
    # "Additional details (optional)" fieldset, which is most of the form's height.
    rec.shot_region(6, 'fieldset:has(#div_id_name)', 'fieldset:has(#div_id_pricing_type)')
    rec.submit('save the artwork', artwork_form)

    # Step 7 — "on your Me page the show appears under 'Shows Accepting Submissions'
    #           with a Submit button... select the artwork card you just added, and
    #           click Submit."
    rec.at_step(7)
    rec.click('go to your Me page', rec.control('Me'))
    rec.expect_text('find the "Shows Accepting Submissions" section',
                    'Shows Accepting Submissions')
    rec.goto(f'/show/{facts["show_slug"]}/submit/')
    # The card, not the radio behind it: the radio is `display: none` so the whole card
    # is the click target, which is what the guide describes and what a reader does.
    rec.click('select the artwork card you just added',
              rec.page.get_by_text(CAPTURE_ARTWORK_TITLE, exact=True))
    rec.shot(7, selector='#artwork-cards')
    rec.click('click Submit', rec.page.locator('#submit-btn'))
    rec.page.wait_for_load_state('load')

    # Step 9 — "Your submission appears on your Me page ... as 'Submitted'." Step 8 is
    # the submission-cap rule, which the seeded show does not set, so there is nothing to
    # photograph — it is declared prose-only rather than skipped.
    rec.at_step(9)
    rec.click('go to your Me page', rec.control('Me'))
    rec.expect_text('see your submission listed', facts['show_name'])
    rec.shot(9, selector='.card:has(button:has-text("Retract"))')

    # Step 10 — "click Retract next to your artwork". Cropped tighter than step 9:
    # here the Retract control is the subject, not the whole card.
    rec.shot(10, selector='.card__info:has(button:has-text("Retract"))')


# ── how-to-sign-up-for-an-account ────────────────────────────────────────────

def prepare_sign_up():
    return {}


def capture_sign_up(rec, facts):
    """Signup on its own: the menu, the form, confirmation, and what you land on.

    Overlaps the submission guide's opening steps by design — someone creating an account
    should not have to read the submission guide — but the framing differs: this one
    photographs the Account menu itself, and ends on the auto-created profile rather than
    carrying on into adding work.
    """
    # Step 1 — "Open the Account menu in the navigation and choose Sign Up."
    rec.at_step(1)
    rec.goto('/')
    rec.click('open the Account menu', rec.control('Account'))
    # The open dropdown is the subject: this step is about finding Sign Up, not filling
    # anything in. Bootstrap adds .show to the menu it has just opened.
    rec.shot_region(1, '.dropdown-menu.show')
    rec.click('choose Sign Up', rec.control('Sign Up'))

    # Step 2 — "Enter your first name, last name, email address, and password — or use
    #           the 'Continue with Google' option to skip the password."
    rec.at_step(2)
    signup_form = _fill_signup(rec)
    rec.shot(2, selector=signup_form)
    rec.submit('submit the signup form', signup_form)

    # Step 3 — "you land on a 'Verify Your Email Address' page ... Open it, click
    #           Confirm, then log in with the email and password you chose."
    rec.at_step(3)
    rec.expect_text('land on a "Verify Your Email Address" page',
                    'Verify Your Email Address')
    confirm_form = _open_email_confirmation(rec)
    rec.shot_region(3, 'h1', confirm_form)
    rec.submit('click Confirm', confirm_form)
    rec.submit('log in', _fill_login(rec))

    # Step 4 — "an artist profile is automatically created for you using your name and
    #           email."
    rec.at_step(4)
    if '/edit/' not in rec.page.url:
        raise DocumentationMismatch(
            f'step 4: the guide says an artist profile is created for you, but after '
            f'logging in the reader is on {rec.page.url} rather than that profile.\n'
            f'    Either the post-login redirect changed, or the step is now wrong.')
    rec.shot_region(4, 'fieldset:has(#div_id_zipcode)')

    # Step 5 — "visit your artist edit page after signing in — you will see a link to
    #           claim your existing record."
    rec.shot_region(5, '.alert-info')


# ── how-to-complete-your-artist-profile ──────────────────────────────────────

def prepare_complete_profile():
    """A signed-up-but-empty artist, which is what this guide's reader is."""
    artist = _create_verified_artist(CAPTURE_EMAIL, complete=False)
    return {'artist_pk': artist.pk}


def capture_complete_profile(rec, facts):
    """Filling in a profile, reached the way a returning reader reaches it."""
    _log_in(rec)

    # Step 1 — "After signing up you are taken directly to your artist profile edit
    #           page — fill in your details there."
    rec.at_step(1)
    rec.goto(f'/artist/{facts["artist_pk"]}/edit/')
    _fill_profile(rec)
    rec.shot_region(1, 'fieldset:has(#div_id_zipcode)')

    # Step 2 — "sign in — you will be taken to your Me page. Click Edit on your artist
    #           profile."
    rec.at_step(2)
    rec.click('go to your Me page', rec.control('Me'))
    # The whole profile card: "click Edit on your artist profile" is only findable if the
    # reader can see which card the link belongs to.
    rec.shot_region(2, '.card:has(a[href*="/edit/"])')

    # Step 3 — "Fill in your bio, statement, website, Instagram handle, Venmo, and
    #           upload a profile photo."
    rec.at_step(3)
    rec.click('click Edit on your artist profile',
              rec.page.locator('a[href*="/edit/"]'))
    rec.fill('fill in your bio', '[name="bio"]', 'Painter working in oil, based in Berkeley.')
    rec.fill('fill in your statement', '[name="statement"]',
             'I paint the light in rooms I have lived in.')
    rec.fill('fill in your website', '[name="website"]', 'tesstester.com')
    rec.fill('fill in your Instagram handle', '[name="instagram"]', '@tesstester')
    rec.fill('fill in your Venmo', '[name="venmo"]', '@tess-tester')
    rec.shot_region(3, 'fieldset:has(#div_id_bio)')

    # Step 4 — "The email on your artist record is your public contact address ...
    #           Update it on the edit page if needed."
    rec.shot_region(4, '#div_id_email')
    # Step 5 is about when the profile becomes public — no screen of its own.


# ── how-to-add-artworks ──────────────────────────────────────────────────────

def prepare_add_artworks():
    artist = _create_verified_artist(CAPTURE_EMAIL, complete=True)
    return {'artist_pk': artist.pk}


def capture_add_artworks(rec, facts):
    """The artwork form in its own right, including the fields the submit guide skips."""
    _log_in(rec)

    # Step 1 — "Sign in ... Find the Artworks section and click the New button."
    rec.at_step(1)
    rec.click('go to your Me page', rec.control('Me'))
    # From the "Artworks" heading down to the New link, so the step's "find the Artworks
    # section" is visible rather than just the button it ends at. Named by text because
    # the Me page has several identical .section-label blocks and this is not the first.
    rec.shot_region(1, '.section-label:has-text("Artworks")', 'a[href*="/artwork/new/"]')

    # Step 2 — "Fill in the title, year, medium, dimensions ... and upload an image."
    rec.at_step(2)
    rec.click('click the New button under Artworks', rec.control('New'))
    _fill_artwork(rec)
    rec.shot_region(2, 'fieldset:has(#div_id_name)')

    # Step 3 — "Choose the Pricing option ... 'For Sale' also requires a price."
    rec.shot_region(3, 'fieldset:has(#div_id_pricing_type)')

    # Step 4 — "fill in the optional 'Framed size' ... under Additional details."
    rec.at_step(4)
    rec.fill('fill in the framed width', '[name="framed_width_inches"]', '26')
    rec.fill('fill in the framed height', '[name="framed_height_inches"]', '38')
    # Depth too, and not only for completeness: artwork_form_validate.js inserts its hint
    # <div> lazily on a field's first input event, so leaving one of the three untouched
    # left that column a different height and made the clip box vary by 2px between runs.
    rec.fill('fill in the framed depth', '[name="framed_depth_inches"]', '2')
    # The whole crispy row rather than a union of two of its columns: a computed union
    # tracked whichever column happened to be tallest, which varied by 2px between runs.
    rec.shot(4, selector='div.row:has(#div_id_framed_width_inches)')

    # Step 5 — "Optionally record the 'Hang drop' (under Additional details)."
    rec.at_step(5)
    rec.fill('record the hang drop', '[name="hang_drop_inches"]', '3')
    rec.shot_region(5, '#div_id_hang_drop_inches')
    # Step 6 is about newly added work staying private — no screen of its own.


# ── how-to-pin-artworks ──────────────────────────────────────────────────────

def prepare_pin_artworks():
    """Needs an account plus an artwork the reader can actually see and pin.

    Pinning is the first captured flow that acts on *someone else's* work, so it depends
    on the seeded shows having published artwork rather than on anything the script makes.
    """
    artist = _create_verified_artist(CAPTURE_EMAIL, complete=True)
    from gallery.permissions import visible_artwork_queryset

    User = get_user_model()
    artwork = (Artwork.objects.filter(visible_artwork_queryset(artist.user))
               .exclude(artists__user=artist.user).distinct().order_by('pk').first())
    if artwork is None:
        raise CommandError(
            'No publicly visible artwork by another artist, so there is nothing to pin. '
            'Re-seed with `bash scripts/create_test_database.sh`.')
    return {'artist_pk': artist.pk, 'artwork_name': artwork.name}


def capture_pin_artworks(rec, facts):
    """Pinning from a card, and the private pinboard it builds."""
    _log_in(rec)

    # Step 1 — "Sign in. Browse any show or the Artworks gallery."
    rec.at_step(1)
    rec.goto('/artworks/')
    rec.shot(1)

    # Step 2 — "On any artwork card, click the 📌 Pin button in the card footer. The
    #           button turns orange and shows 'Pinned'."
    rec.at_step(2)
    card = f'.card:has-text("{facts["artwork_name"]}")'
    pin = rec.page.locator(f'{card} .save-artwork-btn').first
    rec.click('click the Pin button in the card footer', pin)
    # Pinning is an AJAX POST with no navigation, so wait for the label the step promises
    # rather than for a page load — and failing here means the button no longer confirms
    # itself the way the guide says it does.
    rec.expect_text('see the button show "Pinned"', 'Pinned')
    # The whole card, not just its footer strip: the step is about recognising the pinned
    # state on a card, which needs the card.
    rec.shot(2, selector=card)

    # Step 4 — "Pinned artworks appear on your artist profile page under 'Pinned'."
    rec.at_step(4)
    rec.click('go to your Me page', rec.control('Me'))
    rec.expect_text('find the "Pinned" section', 'Pinned')
    rec.shot_region(4, '#pinned-sortable')

    # Step 6 — "To unpin, click the orange 📌 Pinned button again."
    rec.shot(6, selector='#pinned-sortable .card')
    # Step 3 (pinning from inside a slideshow) and step 5 (drag to reorder) have no
    # single still that shows them; both are declared prose-only.


# ── how-to-buy-a-piece-of-artwork ────────────────────────────────────────────

def prepare_buy_artwork():
    """An inquirable artwork, plus an account for the optional Claim step at the end.

    `can_inquire` is true only when one of the artwork's artists has an email address
    (gallery/views/artworks.py), so this looks for that rather than assuming any seeded
    piece will show the button.
    """
    artist = _create_verified_artist(CAPTURE_EMAIL, complete=True)
    from gallery.permissions import visible_artwork_queryset

    artwork = (Artwork.objects
               .filter(visible_artwork_queryset(artist.user))
               .filter(artists__email__isnull=False)
               .exclude(artists__email='')
               .exclude(artists__user=artist.user)
               .distinct().order_by('pk').first())
    if artwork is None:
        raise CommandError(
            'No publicly visible artwork has an artist with an email address, so the '
            'Inquire button never appears. Re-seed with '
            '`bash scripts/create_test_database.sh`.')
    return {'artwork_url': artwork.get_absolute_url(), 'artwork_name': artwork.name}


def capture_buy_artwork(rec, facts):
    """Enquiring about a piece, and the optional claim once you own it.

    The first five steps need no account — enquiring is deliberately open to anyone — so
    the script stays signed out until the guide tells the reader to create one.
    """
    # Step 1 — "browse Shows or Artists from the navigation"
    rec.at_step(1)
    rec.goto('/artworks/')
    rec.shot(1)

    # Step 2 — "On the artwork detail page, check the price listed."
    rec.at_step(2)
    rec.goto(facts['artwork_url'])
    # The whole detail card, not the bare price line: cropped to the <p> alone this was
    # 388x14 px of text with nothing to say which piece it described.
    rec.shot_region(2, '.card:has(p:has-text("Price:"))')

    # Step 3 — "Click the Inquire button ... No account is required to inquire."
    rec.at_step(3)
    rec.shot_region(3, '.card__info:has(a:has-text("Inquire"))')
    rec.click('click the Inquire button', rec.control('Inquire'))

    # Step 4 — "Fill in your name, email address, and a message to the artist."
    rec.at_step(4)
    inquiry_form = rec.form_with('[name="message"]')
    rec.fill('fill in your name', f'{inquiry_form} [name="sender_name"]', 'Jane Doe')
    rec.fill('fill in your email address', f'{inquiry_form} [name="sender_email"]',
             'jane@example.com')
    rec.fill('write a message to the artist', f'{inquiry_form} [name="message"]',
             'I saw this piece in the current show and would love to know more about it. '
             'Is it still available?')
    rec.shot(4, selector=inquiry_form)

    # Step 5 — "Submit the form. Your message is sent directly to the artist."
    # The inquiry view embeds a signed timestamp and rejects anything submitted within
    # _INQUIRY_MIN_FILL_SECONDS (3s) as a bot. A script fills the form in milliseconds, so
    # it has to wait — deliberately, not as a flake workaround.
    rec.page.wait_for_timeout(3_500)
    rec.submit('submit the form', inquiry_form)
    rec.at_step(5)
    rec.expect_text('see that the inquiry was sent', 'has been sent to the artist')
    rec.shot_region(5, '.alert')

    # Steps 6-8 are the email exchange afterwards and pointers to the account and profile
    # guides — nothing on screen for any of them.

    # Step 9 — "click 'Claim' in the button bar." Only signed-in readers see it, which is
    # why the guide puts creating an account in step 8.
    rec.at_step(9)
    _log_in(rec)
    rec.goto(facts['artwork_url'])
    rec.shot_region(9, '.card__info:has(button:has-text("Claim"))')


# ── how-to-adjust-card-sizes ─────────────────────────────────────────────────

CARD_SIZE_WIDGET = '#card-size-control'


def prepare_adjust_card_sizes():
    return {}


def capture_adjust_card_sizes(rec, facts):
    """The card-size control: where it is, and what moving it does.

    A drag cannot be photographed, so this sets the slider and photographs the *effect* —
    which is the point of the control — rather than pretending to show a gesture.
    """
    def set_size(percent):
        """Move the slider as a drag would, including the events its JS listens for."""
        rec.page.evaluate(
            """(pct) => {
                 const s = document.getElementById('card-size-slider');
                 s.value = String(pct / 100);
                 s.dispatchEvent(new Event('input', {bubbles: true}));
                 s.dispatchEvent(new Event('change', {bubbles: true}));
               }""", percent)

    # Step 1 — "On any page that shows a card grid ... a ▦ icon appears in the
    #           bottom-right corner of the browser window."
    rec.at_step(1)
    rec.goto('/artworks/')
    # A viewport shot, not the widget alone: this step is about *where* the control is, so
    # the corner it sits in is as much the subject as the control.
    rec.expect_visible('see the card size control appear in the corner',
                       CARD_SIZE_WIDGET)
    rec.shot(1)

    # Step 2 — "Drag the slider ... The percentage label updates live."
    rec.at_step(2)
    set_size(150)
    rec.shot(2, selector=CARD_SIZE_WIDGET)

    # Step 3 — "The size range is 25% ... to 200% (large cards)."
    rec.at_step(3)
    set_size(200)
    rec.shot(3)

    # Step 4 — double-clicking the icon to reset looks exactly like step 1 once it has
    # happened, so there is no second picture worth taking.


# ── Jury and curation ────────────────────────────────────────────────────────
# All three run against the seeded Feel-Full show, which is in review with 26
# submissions: four scored by both jurors and twenty-two deliberately left pending, so
# "Pending Review" has something in it and the curator view has both scored and unscored
# work. The rubric is the real All Form No Function one. See create_test_database.sh.

JURY_SHOW_SLUG = 'feel-full'
JUROR_EMAIL = 'juror1@example.com'
CURATOR_EMAIL = 'jonathan@bachrach.com'
SEEDED_PASSWORD = 'b8'


def _jury_show():
    """The seeded in-review show, checked rather than assumed."""
    show = Show.objects.filter(slug=JURY_SHOW_SLUG).first()
    if show is None or show.status != Show.STATUS_IN_REVIEW:
        raise CommandError(
            f'"{JURY_SHOW_SLUG}" is missing or not in review, so there is nothing to '
            f'jury. Re-seed with `bash scripts/create_test_database.sh`.')
    if not show.rubric_criteria.exists():
        raise CommandError(
            f'"{JURY_SHOW_SLUG}" has no rubric, so the per-criterion scoring these '
            f'guides describe does not appear. Re-seed.')
    return show


# Reviews that existed before a capture started. The review-slideshow script scores an
# artwork for real — that is the step it is illustrating — which would otherwise leave a
# review behind on the seeded show, accumulating across runs and quietly changing the
# jury data every other guide is captured against. Module-level because the registry's
# cleanup hook takes no arguments, and the runner is strictly sequential.
_JURY_REVIEW_BASELINE = set()


def prepare_jury():
    show = _jury_show()
    global _JURY_REVIEW_BASELINE
    _JURY_REVIEW_BASELINE = set(
        ArtworkReview.objects.filter(show=show).values_list('pk', flat=True))
    return {'slug': show.slug,
            'reviews_url': f'/show/{show.slug}/reviews/',
            'criteria': show.rubric_criteria.count()}


def _cleanup_jury():
    """Undo scoring done during the run, then the usual throwaway-account cleanup."""
    show = Show.objects.filter(slug=JURY_SHOW_SLUG).first()
    if show is not None and _JURY_REVIEW_BASELINE is not None:
        (ArtworkReview.objects
         .filter(show=show)
         .exclude(pk__in=_JURY_REVIEW_BASELINE)
         .delete())
    _reset_capture_account()


def _open_review_slideshow(rec):
    """Launch the review slideshow and wait for it to finish loading."""
    rec.click('click the "Review Slideshow" button',
              rec.page.locator('.rs-launch-btn'))
    rec.expect_visible('see the slideshow open full-screen', '#review-overlay.rs-open')
    # The first artwork arrives over fetch, so the panel is empty for a moment.
    rec.expect_visible('see the artwork and its scoring rows', '#rs-criteria .rs-score-row')


def capture_jury_a_show(rec, facts):
    """The juror's path: Reviews page, pending list, and scoring one piece on its own."""
    _log_in(rec, JUROR_EMAIL, SEEDED_PASSWORD)

    # Steps 1-2 are having an account and being assigned by a curator — no screen.

    # Step 3 — "go to Shows. Open the show you are jurying and click Reviews."
    rec.at_step(3)
    rec.goto(f'/show/{facts["slug"]}/')
    # Reviews is a dropdown item under "Curate", not a button on the page — which is what
    # this step used to claim. Open the menu so the screenshot shows where it actually is.
    rec.click('open the Curate menu', rec.control('Curate'))
    rec.expect_visible('see Reviews in the menu', '.dropdown-menu.show')
    rec.shot_region(3, '.dropdown-menu.show')

    # Step 4 — "a Pending Review section listing all artworks you have not yet scored."
    rec.at_step(4)
    rec.goto(facts['reviews_url'])
    rec.shot_region(4, '.section-label:has-text("Pending Review")')

    # Step 5 — "click Review on any individual artwork to score it on its own page."
    rec.at_step(5)
    rec.shot_region(5, '.card:has(a:has-text("Review"))')

    # Step 6 — "If the curator has defined a rubric, score each criterion individually."
    rec.at_step(6)
    rec.click('click Review on an individual artwork',
              rec.page.locator('a:has-text("Review")'))
    # The scoring form, not `form` — the navbar's search form comes first in the DOM and
    # has no bounding box, so a bare 'form' selector silently found nothing to clip.
    review_form = rec.form_with('.score-radios')
    rec.shot(6, selector=review_form)

    # Step 7 — "Optionally add review notes ... click Submit review."
    rec.at_step(7)
    rec.shot_region(7, f'{review_form} textarea',
                    f'{review_form} button[type="submit"]')

    # Steps 8-10 are returning to change scores, how averaging is used, and the
    # curator-who-is-also-a-juror case — none has a screen of its own.


def capture_review_slideshow(rec, facts):
    """The juror's full-screen scoring tool, including its help overlay."""
    _log_in(rec, JUROR_EMAIL, SEEDED_PASSWORD)
    rec.goto(facts['reviews_url'])

    # Step 1 — "click the 'Review Slideshow' button next to the 'Pending Review' heading."
    rec.at_step(1)
    rec.shot_region(1, '.section-label:has-text("Pending Review")')
    _open_review_slideshow(rec)

    # Step 2 — "The artwork image fills the left side. The right side shows the title,
    #           artists, and one scoring button row per rubric criterion."
    rec.at_step(2)
    rec.shot(2)

    # Step 3 — "Click a score button ... Your score saves instantly."
    rec.at_step(3)
    rec.click('click a score button', rec.page.locator('.rs-score-btn').first)
    rec.shot(3, selector='#rs-criteria')

    # Step 4 — "advances to the next unscored artwork (if Auto is checked in the top bar)."
    rec.at_step(4)
    rec.shot(4, selector='#rs-topbar')

    # Steps 5, 6 are arrow keys and number keys — gestures, not screens.

    # Step 7 — "Thumbnail strip at the bottom: gold = partially scored, green = fully
    #           scored, teal outline = current."
    rec.at_step(7)
    rec.shot(7, selector='#rs-thumbs')

    # Steps 8-10 are opening the detail page, coming back, and closing.

    # Step 11 — "Press ? for a quick keyboard reference."
    rec.at_step(11)
    rec.click('press ? for the keyboard reference',
              rec.page.locator('#rs-help-btn'))
    rec.expect_visible('see the keyboard reference', '#rs-help-inner')
    rec.shot(11, selector='#rs-help-inner')


def prepare_curation():
    show = _jury_show()
    global _JURY_REVIEW_BASELINE
    _JURY_REVIEW_BASELINE = set(
        ArtworkReview.objects.filter(show=show).values_list('pk', flat=True))
    return {'slug': show.slug, 'reviews_url': f'/show/{show.slug}/reviews/'}


def capture_curation_slideshow(rec, facts):
    """The curator's decision tool: jury scores on the right, three decision buttons."""
    _log_in(rec, CURATOR_EMAIL, SEEDED_PASSWORD)
    rec.goto(facts['reviews_url'])

    # Step 1 — "click the 'Curation Slideshow' button next to the 'Artworks' heading."
    rec.at_step(1)
    rec.shot_region(1, '.section-label:has-text("Artworks")')
    # By its label, not `.cs-launch-btn.first`: the Juror Progress table has its own
    # launch buttons earlier in the DOM, labelled "Slideshow", and they pass ?juror=<id>
    # so the panel shows a single juror's scores. That is step 12's control. Clicking it
    # here quietly produced a "REVIEWS (1)" panel for a step about seeing every juror.
    rec.click('click the "Curation Slideshow" button',
              rec.page.get_by_role('button', name='Curation Slideshow'))
    rec.expect_visible('see the slideshow open full-screen', '#cs-overlay.cs-open')
    rec.expect_visible('see the jury scores panel', '#cs-scores')

    # Step 2 is the ordering (highest score first) — a property of the sequence, not a
    # screen.

    # Step 3 — "image fills the left side. The right side shows title, artists, year,
    #           medium, dimensions."
    rec.at_step(3)
    rec.shot(3)

    # Step 4 — "each juror's name, their score on each criterion, and their weighted
    #           total. The overall score ... at the top in teal."
    rec.at_step(4)
    rec.shot(4, selector='#cs-scores')

    # Step 5 — "Weak scores (the lowest rating) are shown in red." The seed gives one
    # artwork a lowest-rating score precisely so this has something to point at; if the
    # sequence does not reach it, say so rather than shipping a picture of nothing.
    rec.at_step(5)
    # The sequence runs highest score first, so the piece carrying the lowest rating is
    # near the end — advance until one is on screen rather than photographing whatever
    # happens to be first and calling it red.
    for _ in range(12):
        if rec.page.locator('.cs-score-weak').count():
            break
        rec.page.keyboard.press('ArrowRight')
        rec.page.wait_for_timeout(350)
    else:
        raise DocumentationMismatch(
            'step 5: the guide says the weakest rating renders in red, but no '
            '.cs-score-weak appeared in the first 12 artworks.\n'
            '    The seed gives one piece a lowest-rating score for exactly this step — '
            'check create_test_database.sh still does.')
    rec.shot_region(5, '.cs-juror-block:has(.cs-score-weak)')

    # Step 6 — "three decision buttons: Undecided, Selected, and Rejected."
    rec.at_step(6)
    rec.shot(6, selector='#cs-decision-area')

    # Step 7 — "The top bar shows a running count."
    rec.at_step(7)
    rec.shot(7, selector='#cs-counter-area')

    # Step 8 — "Thumbnail strip at the bottom: green = selected, red = rejected ..."
    rec.at_step(8)
    rec.shot(8, selector='#cs-thumbs')

    # Steps 9-11 are keyboard and navigation.

    # Step 12 — "click the 'Slideshow' button next to that juror's name in the Juror
    #            Progress table."
    rec.at_step(12)
    rec.page.keyboard.press('Escape')
    rec.goto(facts['reviews_url'])
    rec.shot_region(12, '.section-label:has-text("Juror Progress")',
                    'table:has(.cs-launch-btn)')

    # Step 13 is closing and the separate publish workflow.


# ── Curator lifecycle guides ─────────────────────────────────────────────────
# These drive a show through real status changes, invite artists, and add work. They must
# not do any of that to a seeded show: `working-craft` is the invitation-only fixture other
# guides and the submission flows depend on, and publishing or closing it would quietly
# change what every other capture sees. So each run builds its own show and deletes it
# afterwards. The slug prefix is what makes cleanup reliable even after a failed run.

CAPTURE_SHOW_PREFIX = 'howto-capture'
CAPTURE_SHOW_NAME_PREFIX = 'Howto Capture'


def _create_capture_show(name, submission_type, status=None, days_open=30):
    """A throwaway show owned by this capture run, deleted in cleanup.

    Dates are relative to today for the same reason the seed script's are: a hard-coded
    deadline in the past makes a show accept nothing, which looks like a bug in the flow
    rather than a stale fixture.
    """
    import datetime as dt

    from gallery.models import Site

    today = dt.date.today()
    # The prefix goes in the *name*: Show.save() regenerates the slug from the name, so a
    # slug set here is discarded and cleanup's slug__startswith filter matches nothing.
    show = Show.objects.create(
        name=f'{CAPTURE_SHOW_NAME_PREFIX} {name}',
        start=today + dt.timedelta(days=days_open + 30),
        end=today + dt.timedelta(days=days_open + 60),
        submission_deadline=today + dt.timedelta(days=days_open),
        submission_type=submission_type,
        status=status or Show.STATUS_UNDER_CONSIDERATION,
    )
    site = Site.objects.first()
    if site is not None:
        show.sites.add(site)
    curator = Artist.objects.filter(user__email=CURATOR_EMAIL).first()
    if curator is not None:
        show.curators.add(curator)
    return show


def _cleanup_capture_shows():
    """Delete every show this capture machinery has ever created, then the account."""
    Show.objects.filter(slug__startswith=CAPTURE_SHOW_PREFIX).delete()
    Artist.objects.filter(name=ON_BEHALF_ARTIST_NAME, user__isnull=True).delete()
    _reset_capture_account()


# ── how-to-add-artwork-on-behalf-of-an-artist ────────────────────────────────

ON_BEHALF_ARTIST_NAME = 'Wren Halloway'


def prepare_add_on_behalf():
    """An invitation-only show plus an artist with no account — the guide's whole premise.

    The artist is created here rather than through the UI because step 2 is about the
    Artists → New form, which the script photographs empty; creating a second one through
    it would leave a duplicate behind.
    """
    _cleanup_capture_shows()
    show = _create_capture_show('Howto On Behalf', Show.SUBMISSION_INVITED,
                                Show.STATUS_OPEN_CALL)
    artist = Artist.objects.create(
        name=ON_BEHALF_ARTIST_NAME, first_name='Wren', last_name='Halloway',
        zipcode='94710', email='')
    artwork = Artwork.objects.create(
        name='Folded Light', end_year=2025, medium='Paper and thread',
        width_inches=18, height_inches=24,
        pricing_type=Artwork.PRICING_ON_REQUEST)
    artwork.artists.add(artist)
    return {'slug': show.slug, 'artist_pk': artist.pk,
            'artist_name': ON_BEHALF_ARTIST_NAME}


def capture_add_on_behalf(rec, facts):
    """Adding work for an artist who has no account of their own."""
    _log_in(rec, CURATOR_EMAIL, SEEDED_PASSWORD)

    # Step 1 is when to use this at all — no screen.

    # Step 2 — "create their profile first (Artists → New) ... Leave 'Linked user
    #           account' blank." This 403d for curators until the capture found it.
    rec.at_step(2)
    rec.goto('/artist/new/')
    rec.shot_region(2, 'fieldset:has(#div_id_zipcode)')

    # Step 3 — "Go to the show's Submissions page and click 'Add artwork on behalf of an
    #           artist'."
    rec.at_step(3)
    rec.goto(f'/show/{facts["slug"]}/submissions/')
    rec.shot_region(3, 'a:has-text("Add artwork on behalf")')

    # Step 4 — "Choose the artist from the dropdown. The page then lists that artist's
    #           existing artworks."
    rec.at_step(4)
    rec.click('click "Add artwork on behalf of an artist"',
              rec.page.get_by_role('link', name='Add artwork on behalf of an artist'))
    rec.select('choose the artist from the dropdown', '#artist-select',
               str(facts['artist_pk']), navigates=True)
    rec.shot_region(4, 'form:has(#artist-select)')

    # Step 5 — "Either select one of their existing artworks ... OR fill in the 'Create a
    #           new artwork' form."
    rec.at_step(5)
    rec.shot_region(5, 'form:has(button[value="add_existing"])')

    # Step 6 — "The piece is added to the show as a curator-selected submission."
    rec.at_step(6)
    rec.click('select one of their existing artworks',
              rec.page.locator('input[name="artwork"]').first)
    rec.submit('click "Add selected artwork to show"',
               'form:has(button[value="add_existing"])')
    rec.goto(f'/show/{facts["slug"]}/submissions/')
    rec.expect_text('see the piece on the Submissions page', facts['artist_name'])
    rec.shot_region(6, f'.card:has-text("{facts["artist_name"]}")')

    # Step 7 is linking the profile to an account later — a staff page, its own guide.


# ── how-to-run-an-invitation-only-show ───────────────────────────────────────

def _add_decided_submissions(show, accepted=4, rejected=2):
    """Give a capture show real submissions with curator decisions on them.

    Three steps depend on this and all three were illustrated with empty pages:

    - "Send Emails" is only built when `emails_pending or emails_sent`
      (show_actions.py), and emails_pending counts accepted/rejected submissions on a
      published show whose notification has not gone out. With none, the Logistics menu
      contained only "Emails" — the artist address list, a different thing entirely — so
      the step that says to click "Send Emails" showed a menu without it.
    - The Publish confirmation page had no diff to show, because nothing was selected.
    - The Submissions page had no cards on it.
    """
    from gallery.permissions import visible_artwork_queryset

    User = get_user_model()
    staff = User.objects.filter(is_staff=True).first()
    # Distinct titles, not just distinct rows. The seed has two artworks called "Oliver"
    # and two called "Drawing" (one of each per show), so taking the first N by pk put the
    # same title under both "Adding" and "Rejecting" on the publish page — correct data
    # that reads as a bug in a screenshot.
    pool, seen_titles, seen_artists = [], set(), set()
    for artwork in (Artwork.objects.filter(visible_artwork_queryset(staff))
                    .distinct().prefetch_related('artists').order_by('pk')):
        artist = artwork.artists.first()
        key = artist.pk if artist else None
        if artwork.name in seen_titles or key in seen_artists:
            continue
        seen_titles.add(artwork.name)
        seen_artists.add(key)
        pool.append(artwork)
        if len(pool) == accepted + rejected:
            break
    for index, artwork in enumerate(pool):
        selected = index < accepted
        ArtworkSubmission.objects.get_or_create(
            show=show, artwork=artwork,
            defaults={
                'status': (ArtworkSubmission.ACCEPTED if selected
                           else ArtworkSubmission.REJECTED),
                'curator_decision': (ArtworkSubmission.CURATOR_SELECTED if selected
                                     else ArtworkSubmission.CURATOR_REJECTED),
            })
    return len(pool)


def prepare_invitation_show():
    _cleanup_capture_shows()
    show = _create_capture_show('Howto Invitational', Show.SUBMISSION_INVITED)
    _add_decided_submissions(show)
    return {'slug': show.slug, 'pk': show.pk}


def capture_invitation_show(rec, facts):
    """A whole invitation-only show, start to finish, on a show made for the run."""
    _log_in(rec, CURATOR_EMAIL, SEEDED_PASSWORD)
    show_url = f'/show/{facts["slug"]}/'

    # Step 1 — "Create the show (staff only) with Submission Type set to 'Invited' and a
    #           submission deadline."
    rec.at_step(1)
    rec.goto('/show/new/')
    # The whole form runs to ~1960px. This step names Submission Type and the deadline,
    # so crop to that run of fields — the form has no fieldsets to lean on.
    rec.shot_region(1, '#div_id_submission_type', '#div_id_decision_date')

    # Step 2 — "click 'Invite Artists' to add artists by email address."
    rec.at_step(2)
    rec.goto(f'{show_url}invite/')
    rec.fill('add artists by email address', 'textarea',
             'wren@example.com\nsasha@example.com\nlee@example.com')
    rec.shot_region(2, 'form:has(textarea)')
    rec.submit('save the invitations', rec.form_with('textarea'))

    # Step 3 — "a progress table: account, profile, artworks, submitted, emailed,
    #           Accepted."
    rec.at_step(3)
    rec.shot_region(3, 'table')

    # Step 4 — "controls to fix a wrong email in place, Resend, or Copy link."
    rec.at_step(4)
    rec.shot_region(4, 'table tbody tr')

    # Step 5 is what the invitation email contains — not a page in the app.

    # Step 6 — "Change the show status to Open Call on the show detail page."
    rec.at_step(6)
    rec.goto(show_url)
    rec.shot(6, selector='form[action*="transition-status"], form[action*="transition"]')

    # Step 7 points at the add-on-behalf guide.

    # Steps 8, 10, 11 are further status changes and the publish confirmation. Drive the
    # show forward through the ORM rather than the status control: the control is already
    # photographed in step 6, and clicking through six transitions makes the run slow and
    # fragile for pictures that would all look the same.
    _db(_advance_capture_show, facts['slug'], Show.STATUS_DRAFT)

    # Step 9 — "Go to the Submissions page ... bulk select ... The action bar at the
    #           bottom moves all selected cards in one step."
    rec.at_step(9)
    rec.goto(f'{show_url}submissions/')
    rec.shot(9)

    # Step 10 — "change the show status to Published ... redirects to the Publish Show
    #            confirmation page."
    rec.at_step(10)
    rec.goto(f'{show_url}promote/')
    rec.shot(10)

    # Step 11 — "Review the diff and click 'Confirm & Publish Show'."
    rec.at_step(11)
    rec.shot_region(11, 'form:has(button:has-text("Confirm"))')

    # Step 12 — "click 'Send Emails' ... The button shows how many are pending."
    rec.at_step(12)
    _db(_advance_capture_show, facts['slug'], Show.STATUS_PUBLISHED)
    rec.goto(show_url)
    rec.click('open the Logistics menu', rec.control('Logistics'))
    rec.expect_visible('see Send Emails in the menu', '.dropdown-menu.show')
    rec.shot_region(12, '.dropdown-menu.show')

    # Step 13 is closing the show — the same status control as step 6.

    # Step 14 — "add events using the New Event link on the show detail page."
    rec.at_step(14)
    rec.click('open the Manage menu', rec.control('Manage'))
    rec.expect_visible('see New Event in the menu', '.dropdown-menu.show')
    rec.shot_region(14, '.dropdown-menu.show')


def _advance_capture_show(slug, status):
    """Move a capture-owned show to a status, refusing to touch anything else."""
    if not slug.startswith(CAPTURE_SHOW_PREFIX):
        raise CommandError(f'refusing to change the status of "{slug}" — not a capture show')
    Show.objects.filter(slug=slug).update(status=status)


# ── show-lifecycle-and-status ────────────────────────────────────────────────

# The status row on the show detail page: the current status and the → transition button
# live in one form, which is exactly what steps 2-8 are about.
STATUS_CONTROL = 'form[action*="transition"]'


def prepare_show_lifecycle():
    _cleanup_capture_shows()
    show = _create_capture_show('Lifecycle', Show.SUBMISSION_OPEN)
    return {'slug': show.slug}


def capture_show_lifecycle(rec, facts):
    """One show walked through every status, photographed at each.

    Statuses are set through the ORM rather than by clicking the → button six times.
    Several transitions have side effects the guide describes elsewhere — In Review emails
    every juror, Published redirects to the publish confirmation — and firing those to
    take six pictures of a status line would be slow and would send mail.
    """
    _log_in(rec, CURATOR_EMAIL, SEEDED_PASSWORD)
    show_url = f'/show/{facts["slug"]}/'

    def at_status(step, status):
        _db(_advance_capture_show, facts['slug'], status)
        rec.at_step(step)
        rec.goto(show_url)
        # Title through the status row, not the status row alone: these steps are about
        # what each status *means*, and the show header carries the badge and the
        # status-dependent line ("Open Call — Accepting submissions", "In jury review")
        # that actually differ. Seven crops of a bare status line all looked the same.
        rec.shot_region(step, 'h1', STATUS_CONTROL)

    # Step 1 is what a status is for — no screen of its own.

    # Step 2 — "Change the status using the → button shown next to the current status."
    rec.at_step(2)
    rec.goto(show_url)
    rec.shot(2, selector=STATUS_CONTROL)

    # Steps 3-8 — one per status, each showing what the control reads at that point.
    at_status(3, Show.STATUS_UNDER_CONSIDERATION)
    at_status(4, Show.STATUS_OPEN_CALL)
    at_status(5, Show.STATUS_IN_REVIEW)
    at_status(6, Show.STATUS_DRAFT)
    at_status(7, Show.STATUS_PUBLISHED)
    at_status(8, Show.STATUS_CLOSED)

    # Step 9 is who can see which status — a rule, not a screen.


# ── how-to-run-an-open-call-show ─────────────────────────────────────────────

def prepare_open_call_show():
    _cleanup_capture_shows()
    show = _create_capture_show('Open Call', Show.SUBMISSION_OPEN,
                                Show.STATUS_OPEN_CALL)
    _add_decided_submissions(show)
    return {'slug': show.slug}


def capture_open_call_show(rec, facts):
    """A public open call, start to finish. Sibling of the invitation-only guide."""
    _log_in(rec, CURATOR_EMAIL, SEEDED_PASSWORD)
    show_url = f'/show/{facts["slug"]}/'

    # Step 1 — "creates the show with Submission Type set to 'Open' and a submission
    #           deadline."
    rec.at_step(1)
    rec.goto('/show/new/')
    rec.shot_region(1, '#div_id_submission_type', '#div_id_decision_date')

    # Step 2 — "Change the show status to Open Call on the show detail page."
    rec.at_step(2)
    rec.goto(show_url)
    rec.shot(2, selector=STATUS_CONTROL)

    # Step 3 — "define a rubric ... Click 'Manage Rubric Criteria' ... or 'Copy rubric
    #           from another show'."
    rec.at_step(3)
    rec.goto(f'/show/{facts["slug"]}/reviews/rubric/')
    rec.shot(3)

    # Step 4 — "assign jurors now via Assign Jurors on the show detail page."
    rec.at_step(4)
    rec.goto(f'/show/{facts["slug"]}/reviews/jurors/')
    rec.shot(4)

    # Step 5 — "monitor submissions at any time via Submissions on the show detail page."
    rec.at_step(5)
    rec.goto(f'{show_url}submissions/')
    rec.shot(5)

    # Step 6 is another use of the status control, shown in step 2.

    # Step 7 — "Monitor progress on the Reviews dashboard. Use the per-juror Slideshow
    #           buttons in the Juror Progress table." Shown on the seeded show, which has
    #           two jurors with real progress; a show made for this run has none.
    rec.at_step(7)
    rec.goto(f'/show/{JURY_SHOW_SLUG}/reviews/')
    rec.shot_region(7, '.section-label:has-text("Juror Progress")',
                    'table:has(.cs-launch-btn)')

    # Step 8 is another status change.

    # Step 9 — "Artwork cards are sorted by weighted score ... a bar appears at the bottom
    #           ... → Undecided, → Selected, or → Rejected."
    rec.at_step(9)
    rec.goto(f'/show/{JURY_SHOW_SLUG}/submissions/')
    rec.shot(9)

    # Step 10 points at the curation slideshow guide.

    # Step 11 — "change the show status to Published ... redirects to the Publish Show
    #            confirmation page showing what will be added and removed."
    rec.at_step(11)
    _db(_advance_capture_show, facts['slug'], Show.STATUS_DRAFT)
    rec.goto(f'{show_url}promote/')
    rec.shot(11)

    # Step 12 — "Review the diff and click 'Confirm & Publish Show'."
    rec.at_step(12)
    rec.shot_region(12, 'form:has(button:has-text("Confirm"))')

    # Step 13 — "click 'Send Emails' ... shows pending vs. sent counts."
    rec.at_step(13)
    _db(_advance_capture_show, facts['slug'], Show.STATUS_PUBLISHED)
    rec.goto(show_url)
    rec.click('open the Logistics menu', rec.control('Logistics'))
    rec.expect_visible('see Send Emails in the menu', '.dropdown-menu.show')
    rec.shot_region(13, '.dropdown-menu.show')

    # Step 14 is the final status change, again the control from step 2.


# ── how-to-run-a-public-art-site-open-call ───────────────────────────────────

def prepare_public_art_show():
    _cleanup_capture_shows()
    show = _create_capture_show('Public Art', Show.SUBMISSION_OPEN,
                                Show.STATUS_OPEN_CALL)
    _db(_make_public_art, show.slug)
    return {'slug': show.slug}


def _make_public_art(slug):
    if not slug.startswith(CAPTURE_SHOW_PREFIX):
        raise CommandError(f'refusing to modify "{slug}" — not a capture show')
    Show.objects.filter(slug=slug).update(show_type=Show.SHOW_TYPE_PUBLIC_ART)


def capture_public_art_show(rec, facts):
    """A show tied to a physical venue: what differs from a gallery open call."""
    _log_in(rec, CURATOR_EMAIL, SEEDED_PASSWORD)

    # Step 1 is that the venue must exist first — its own guide.

    # Step 2 — "Set Show Type to 'Public Art Site'. In the Sites field, select the venue."
    rec.at_step(2)
    rec.goto('/show/new/')
    rec.shot_region(2, '#div_id_show_type', '#div_id_sites')

    # The guide used to have a step here for a "Location text field" for supplementary
    # address notes. Show has no such field and the form has never rendered one, so the
    # step was describing a control that does not exist; it has been removed rather than
    # reworded. The venue's own address comes from the Sites field above.

    # Step 3 — "Set Submission Type to 'Open' ... and set a submission deadline."
    rec.at_step(3)
    rec.shot_region(3, '#div_id_submission_type', '#div_id_submission_deadline')

    # Step 4 — "Set 'Where artists may be based' if the call is not meant to be local."
    rec.at_step(4)
    rec.shot_region(4, '#div_id_submission_scope')

    # Step 5 — "The show will display a 'Public Art' badge on its card and detail page."
    rec.at_step(5)
    rec.goto(f'/show/{facts["slug"]}/')
    rec.shot_region(5, 'h1', 'p:has-text("Public Art Site")')

    # Step 6 is that everything else matches a gallery open call.


# ── Staff setup guides ───────────────────────────────────────────────────────
# Form-driven and read-only apart from the throwaway site the room-configuration guide
# needs, so these do not disturb the seeded world. Staff, not curator: every page here
# refuses a non-staff user.

STAFF_EMAIL = 'jonathan@bachrach.com'
CAPTURE_SITE_NAME_PREFIX = 'Howto Capture'


def _cleanup_capture_sites():
    from gallery.models import Site
    Site.objects.filter(name__startswith=CAPTURE_SITE_NAME_PREFIX).delete()
    _reset_capture_account()


# ── how-to-create-a-show-staff-only ──────────────────────────────────────────

def prepare_create_show():
    return {}


def capture_create_show(rec, facts):
    """The show creation form, field group by field group."""
    _log_in(rec, STAFF_EMAIL, SEEDED_PASSWORD)

    # Step 1 — "go to Shows in the navigation."
    rec.at_step(1)
    # The canonical listing, not whatever the nav link resolves to: navigation keeps site
    # context, so clicking Shows landed on "Shows at 120710" — true for that session but
    # misleading in a general guide.
    rec.goto('/shows/')
    rec.shot(1)

    # Step 2 — "Click New at the top of the Shows page."
    rec.at_step(2)
    # The whole page-title bar: "at the top of the Shows page" is the useful part, and
    # the link alone cropped to 27x16 px of the word New.
    rec.shot_region(2, '#page-title')

    # Step 3 — "Enter the show name, dates, description, and upload a hero image."
    # The show's start/end dates sit far below, next to the deadlines, so they are in
    # step 5's crop rather than this one — the form's order, not the guide's.
    rec.at_step(3)
    rec.goto('/show/new/')
    rec.shot_region(3, '#div_id_name', '#div_id_image')

    # Step 4 — "Choose a Submission Type."
    rec.at_step(4)
    rec.shot_region(4, '#div_id_submission_type')

    # Step 5 — "Set the Submission Deadline, Review Deadline, and Decision Date."
    rec.at_step(5)
    rec.shot_region(5, '#div_id_submission_deadline', '#div_id_end')

    # Step 6 — "add them in the Curators field."
    rec.at_step(6)
    rec.shot_region(6, '#div_id_curators')

    # Step 7 — "associate the show with a site by selecting it in the Sites field."
    rec.at_step(7)
    rec.shot_region(7, '#div_id_sites')

    # Step 8 — "New shows start with status 'Under Consideration'."
    rec.at_step(8)
    rec.shot_region(8, '#div_id_status')

    # Step 9 is saving, and what the curator does next.


# ── out-of-area-submissions ──────────────────────────────────────────────────

OUT_OF_AREA_ARTISTS = [
    # (name, zipcode, country, artwork title). One domestic but far away, one abroad,
    # one with no location at all — the three flags the guide describes, in the order it
    # describes them. A curator reads these three cases differently, so a screenshot
    # showing only one of them would under-document the page.
    ('Rowan Ashby', '97205', 'US', 'Cascade Study'),
    ('Neve Carlow', 'EC1V 9BD', 'GB', 'Clerkenwell Nocturne'),
    ('Wilder Sant', '', 'US', 'Untitled (No Address)'),
]


def prepare_out_of_area():
    """A show with something to flag: local submissions plus three that are not.

    The artists are made here rather than borrowed from the seed, because the seeded ones
    are all at 94710 and moving one would change what every other guide photographs.
    """
    from django.core.files.base import ContentFile
    from gallery.models import Site

    _cleanup_capture_shows()
    _cleanup_out_of_area_artists()

    show = _create_capture_show('Area Check', Show.SUBMISSION_OPEN,
                                Show.STATUS_OPEN_CALL)
    # Local submissions too: a page where every card is flagged does not show what the
    # flag means, because there is nothing unflagged to read it against.
    _add_decided_submissions(show, accepted=3, rejected=0)

    site = show.sites.first()
    if site is None or not site.submission_zipcodes:
        raise CommandError(
            'The capture show\'s venue has no local postal codes, so nothing can be '
            'flagged and this guide has nothing to photograph. Run:\n'
            '  ./env/bin/python manage.py set_site_catchment 120710 \\\n'
            '      --from-file test_fixtures/bay_area_zipcodes.txt \\\n'
            '      --label "Bay Area (9 counties)"')

    for name, zipcode, country, title in OUT_OF_AREA_ARTISTS:
        first, _, last = name.partition(' ')
        artist = Artist.objects.create(
            name=name, first_name=first, last_name=last,
            zipcode=zipcode, country=country)
        artist.image.save(f'howto-area-{artist.pk}.jpg',
                          ContentFile(_portrait_placeholder()), save=True)
        artwork = Artwork.objects.create(name=title, end_year=2025,
                                         width_inches=14, height_inches=18)
        artwork.artists.add(artist)
        artwork.image.save(f'howto-area-work-{artwork.pk}.jpg',
                           ContentFile(_artwork_placeholder()), save=True)
        ArtworkSubmission.objects.create(show=show, artwork=artwork)

    return {'slug': show.slug, 'pk': show.pk, 'site_slug': site.slug,
            'area_label': site.submission_area_label or 'the area'}


def _set_blind_review(slug, on):
    if not slug.startswith(CAPTURE_SHOW_PREFIX):
        raise CommandError(f'refusing to modify "{slug}" — not a capture show')
    Show.objects.filter(slug=slug).update(blind_review=on)


def _cleanup_out_of_area_artists():
    names = [name for name, _, _, _ in OUT_OF_AREA_ARTISTS]
    Artwork.objects.filter(name__in=[t for _, _, _, t in OUT_OF_AREA_ARTISTS]).delete()
    Artist.objects.filter(name__in=names, user__isnull=True).delete()


def _cleanup_out_of_area():
    _cleanup_capture_shows()
    _cleanup_out_of_area_artists()


def capture_out_of_area(rec, facts):
    """Where the area is configured, and what the flag looks like once it is."""
    _log_in(rec, STAFF_EMAIL, SEEDED_PASSWORD)

    # Step 1 is that this flags rather than blocks — no screen shows that.

    # Step 2 — "Go to Sites, open the venue, and click Edit. Fill in 'Local area name'
    #           ... and 'Local postal codes'."
    rec.at_step(2)
    rec.goto(f'/site/{facts["site_slug"]}/edit/')
    rec.shot_region(2, '#div_id_submission_area_label', '#div_id_submission_zipcodes')

    # Step 3 is the command line, which has no page.

    # Step 4 — "'Where artists may be based' offers three choices."
    rec.at_step(4)
    rec.goto(f'/show/{facts["pk"]}/edit/')
    rec.shot_region(4, '#div_id_submission_scope')

    # Step 5 — "an amber 'Outside area' flag under the artist name."
    rec.at_step(5)
    rec.goto(f'/show/{facts["slug"]}/submissions/')
    rec.expect_text('see the out-of-area flag', 'Outside area')
    rec.shot_region(5, '.card:has-text("Rowan Ashby")')

    # Step 6 — "an artist who has not given a postal code gets a grey 'Location not
    #           given' flag instead."
    rec.at_step(6)
    rec.expect_text('see the unplaced flag', 'Location not given')
    rec.shot_region(6, '.card:has-text("Wilder Sant")')

    # Step 7 — "The summary row ... counts how many are outside the area. Click that
    #           count to see only those."
    rec.at_step(7)
    rec.shot_region(7, '#submission-counts')

    # Step 8 — "During blind review the flag still appears but says only 'Outside area'."
    # Blind review is a setting on the show, not a query parameter, so this switches it
    # on for the shot and off again — leaving it on would change what the next guide's
    # capture of the same page shows.
    rec.at_step(8)
    _db(_set_blind_review, facts['slug'], True)
    try:
        rec.goto(f'/show/{facts["slug"]}/submissions/')
        rec.expect_visible('see the flag with the detail withheld', '.area-flag')
        rec.shot_region(8, '.card:has(.area-flag)')
    finally:
        _db(_set_blind_review, facts['slug'], False)

    # Step 9 is that none of this changes what an artist can do.


# ── how-to-create-and-manage-sites-staff-only ────────────────────────────────

def prepare_manage_sites():
    _cleanup_capture_sites()
    return {}


def capture_manage_sites(rec, facts):
    """Creating a venue: address, geocoding, publication, and its room."""
    _log_in(rec, STAFF_EMAIL, SEEDED_PASSWORD)

    # Step 1 — "click Sites in the navigation."
    rec.at_step(1)
    rec.goto('/sites/')
    rec.shot(1)

    # Step 2 — "Click New Site to open the site creation form."
    rec.at_step(2)
    rec.shot_region(2, '#page-title')

    # Step 3 — "Enter the site name, address fields ... and optionally a hero image and an
    #           icon."
    rec.at_step(3)
    rec.goto('/site/new/')
    rec.shot_region(3, '#div_id_name', '#div_id_icon')

    # Step 4 — "Optionally fill in 'Local area name' and 'Local postal codes'."
    rec.at_step(4)
    rec.shot_region(4, '#div_id_submission_area_label', '#div_id_submission_zipcodes')

    # Step 5 — "click 'Look up coordinates from address' ... review the matched address
    #           shown beneath the button."
    rec.at_step(5)
    rec.shot_region(5, '#geocode-btn', '#geocode-status')

    # Step 6 — "enter the latitude and longitude values manually."
    rec.at_step(6)
    rec.shot_region(6, '#div_id_latitude', '#div_id_longitude')

    # Step 7 — "Set Status to Published."
    rec.at_step(7)
    rec.shot_region(7, '#div_id_status')

    # Step 8 — "In the Gallery Room section ... enter the room dimensions ... and
    #           optionally upload texture images."
    rec.at_step(8)
    rec.shot_region(8, '#div_id_width_in', '#div_id_ceiling_image')

    # Step 9 — "In the Obstacles table, add obstacles such as doors or windows."
    rec.at_step(9)
    rec.shot_region(9, '#obstacle-table')

    # Step 10 is saving.

    # Step 11 — "To edit an existing site, open the site detail page and click Edit."
    rec.at_step(11)
    rec.goto('/site/120710/')
    # The card that carries the link, not the link: cropped to the <a> this was 22x19 px
    # of the word Edit, which does not tell anyone where to find it.
    rec.shot_region(11, '.card:has(a[href*="/edit/"])')

    # Step 12 is deleting, which lives behind that same Edit page.


# ── how-to-configure-a-sites-room-and-walls-staff-only ───────────────────────

def prepare_room_config():
    """A throwaway site, so photographing the room form cannot disturb the real venue."""
    _cleanup_capture_sites()
    from gallery.models import Site
    site = Site.objects.create(
        name=f'{CAPTURE_SITE_NAME_PREFIX} Room', street='1207 10th Street',
        city='Berkeley', state='CA', postal_code='94710', country='US',
        status='published')
    return {'slug': site.slug}


def capture_room_config(rec, facts):
    """The Gallery Room form: dimensions, textures, obstacles and the support catalog."""
    _log_in(rec, STAFF_EMAIL, SEEDED_PASSWORD)
    edit_url = f'/site/{facts["slug"]}/edit/'

    # Step 1 — "Open the site detail page and click Edit, then scroll to the Gallery Room
    #           section."
    rec.at_step(1)
    rec.goto(edit_url)
    rec.shot_region(1, 'h4:has-text("Gallery Room")', '#div_id_height_in')

    # Step 2 — "Enter the room dimensions: width, depth, and height (all in inches)."
    rec.at_step(2)
    rec.shot_region(2, '#div_id_width_in', '#div_id_height_in')

    # Step 3 — "Optionally upload texture images for each surface."
    rec.at_step(3)
    rec.shot_region(3, '#div_id_wall_n_image', '#div_id_ceiling_image')

    # Step 4 — "Scroll to the Obstacles table, under Gallery Room."
    rec.at_step(4)
    rec.shot_region(4, 'h5:has-text("Obstacles")', '#obstacle-table')

    # Step 5 — "For each obstacle, enter a label ... select the wall ... position and
    #           dimensions."
    rec.at_step(5)
    rec.shot_region(5, '#obstacle-table')

    # Steps 6 and 7 are how obstacles render elsewhere, and that corner handles are
    # automatic — neither is a control on this page.

    # Step 8 — "Under 'Supports (catalog)' you define reusable pedestals/shelves."
    rec.at_step(8)
    rec.shot_region(8, 'h5:has-text("Supports")', '#support-table')

    # Step 9 is saving.


# ── how-to-link-an-artist-profile-to-a-user-staff-only ───────────────────────

def prepare_link_artist():
    """Needs an artist with no account. The catalogue artists are exactly that."""
    unlinked = Artist.objects.filter(user__isnull=True).count()
    if not unlinked:
        raise CommandError(
            'No artist records without a user account, so both dropdowns on the link '
            'page would be empty. Re-seed with `bash scripts/create_test_database.sh`.')
    return {'unlinked': unlinked}


def capture_link_artist(rec, facts):
    """Linking a gallery-created artist record to the account that claims it."""
    _log_in(rec, STAFF_EMAIL, SEEDED_PASSWORD)

    # Step 1 — "go to /accounts/link-artists/."
    rec.at_step(1)
    rec.goto('/accounts/link-artists/')
    rec.shot_region(1, 'form:has(select[name="artist"])')

    # Step 2 — "Select the unlinked artist record from the first dropdown."
    rec.at_step(2)
    rec.shot_region(2, 'select[name="artist"]')

    # Step 3 — "Select the user account to link it to from the second dropdown."
    rec.at_step(3)
    rec.shot_region(3, 'select[name="user"]')

    # Step 4 — "Click Link."
    rec.at_step(4)
    rec.shot_region(4, 'select[name="user"]',
                    'form:has(select[name="artist"]) button')

    # Step 5 is what the user can do afterwards.


# ── Install / drop-off scheduling ────────────────────────────────────────────
# The two guides are two halves of one feature: the curator defines windows, the artist
# picks a time inside one. They share a prepare that builds a published show with an
# accepted artwork and real windows, because the artist page 404s without work in the show
# and shows nothing without windows.

SCHEDULING_ARTIST_EMAIL = 'ready@example.com'


def _prepare_scheduling_show(self_install=True):
    import datetime as dt

    from gallery.models.logistics import INSTALL, PICKUP, ScheduleWindow

    _cleanup_capture_shows()
    show = _create_capture_show('Scheduling', Show.SUBMISSION_OPEN,
                                Show.STATUS_PUBLISHED)
    show.self_install = self_install
    show.save(update_fields=['self_install'])

    artist = Artist.objects.filter(user__email=SCHEDULING_ARTIST_EMAIL).first()
    if artist is None:
        raise CommandError(
            f'No seeded artist for {SCHEDULING_ARTIST_EMAIL}, so nobody has accepted work '
            f'in the show and the artist scheduling page would 404. Re-seed.')
    artwork = artist.artworks.first()
    if artwork is None:
        artwork = Artwork.objects.create(
            name='Scheduling Study', end_year=2025, medium='Oil on canvas',
            width_inches=18, height_inches=24,
            pricing_type=Artwork.PRICING_ON_REQUEST)
        artwork.artists.add(artist)
    artwork.shows.add(show)

    # Two ranges of each kind, so the "add as many as you need" step has more than one row
    # to show and the artist's dropdown has a real choice in it.
    base = dt.date.today() + dt.timedelta(days=20)
    for offset, kind in ((0, INSTALL), (1, INSTALL), (40, PICKUP), (41, PICKUP)):
        ScheduleWindow.objects.create(
            show=show, kind=kind, date=base + dt.timedelta(days=offset),
            start=dt.time(10, 0), end=dt.time(16, 0))
    return {'slug': show.slug, 'pk': show.pk, 'artist_pk': artist.pk}


def prepare_schedule_windows():
    return _prepare_scheduling_show(self_install=True)


def capture_schedule_windows(rec, facts):
    """The curator side: defining windows and tracking who has arrived."""
    _log_in(rec, CURATOR_EMAIL, SEEDED_PASSWORD)
    show_url = f'/show/{facts["slug"]}/'

    # Step 1 — "on the show's Edit page, the 'Artists install their own work' setting."
    rec.at_step(1)
    # show_edit is routed by pk, not slug.
    rec.goto(f'/show/{facts["pk"]}/edit/')
    rec.shot_region(1, '#div_id_self_install')

    # Step 2 — "click 'Schedule Windows' in the curatorial button row." It lives in the
    # Logistics menu, so the menu has to be open for the reader to see where.
    rec.at_step(2)
    rec.goto(show_url)
    rec.click('open the Logistics menu', rec.control('Logistics'))
    rec.expect_visible('see Schedule Windows in the menu', '.dropdown-menu.show')
    rec.shot_region(2, '.dropdown-menu.show')

    # Step 3 — "add each date/time range when artists may come ... then click Add."
    rec.at_step(3)
    rec.goto(f'{show_url}schedule-windows/')
    rec.shot(3)

    # Step 4 — "Under 'Pickup windows', do the same."
    # The Pickup heading through its own Add form — the heading alone was 528x16 px of
    # text with none of the section it labels.
    rec.at_step(4)
    rec.shot_region(4, 'h3:has-text("Pickup windows")',
                    'form:has(button:has-text("Add pickup window"))')

    # Step 5 — "To remove a window, click 'remove' next to it." The whole row, so the
    # link has a window next to it; on its own it was 49x18 px.
    rec.at_step(5)
    rec.shot_region(5, 'li:has(button:has-text("remove"))')

    # Step 6 is what the artist then sees — the other guide's subject.

    # Step 7 — "click 'Schedule Tracker' on the show detail page. It lists every accepted
    #           artist with their chosen times."
    rec.at_step(7)
    rec.goto(f'{show_url}schedule-tracker/')
    rec.shot(7)

    # Step 8 — "Tick the Done box in each column ... The summary at the top shows how many
    #           are done."
    rec.at_step(8)
    rec.shot_region(8, 'table')


def prepare_artist_schedule():
    return _prepare_scheduling_show(self_install=True)


def capture_artist_schedule(rec, facts):
    """The artist side: choosing a time inside one of the curator's windows."""
    _log_in(rec, SCHEDULING_ARTIST_EMAIL, SEEDED_PASSWORD)
    show_url = f'/show/{facts["slug"]}/'

    # Step 1 — "open the show detail page and click 'Schedule My Install & Pickup'."
    rec.at_step(1)
    rec.goto(show_url)
    # The whole action row, so the reader can see where among the show's controls the
    # button sits — cropped to the link it was 165x19 px of its own label.
    rec.shot_region(1, '.show-actions')

    # Step 2 — "choose one of the available windows from the dropdown, enter a specific
    #           time within that window's range, and click Set."
    rec.at_step(2)
    rec.goto(f'{show_url}schedule/')
    rec.shot(2)

    # Step 3 — "For Pickup ... do the same."
    rec.at_step(3)
    rec.shot_region(3, 'form:has(select[name*="pickup"]), '
                       'div:has(> h2:has-text("Pickup"))')

    # Step 4 is what install vs drop-off means — a rule, not a control.

    # Step 5 — "Your chosen times are shown on the page." Set one so the step has a
    # chosen time to show rather than an empty form.
    rec.at_step(5)
    _db(_set_artist_schedule, facts['slug'], facts['artist_pk'])
    rec.goto(f'{show_url}schedule/')
    rec.shot(5)

    # Step 6 — "'Add to calendar' links appear next to it."
    # "next to it" is the point of this step, so the crop includes the scheduled time the
    # links sit beside rather than just the two links.
    rec.at_step(6)
    rec.shot_region(6, 'li:has(a:has-text("Google")), p:has(a:has-text("Google"))',
                    'a:has-text("Google")')


def _set_artist_schedule(slug, artist_pk):
    """Book the artist into the first install window, so step 5 has something to show."""
    from gallery.models.logistics import INSTALL, ArtistSchedule, ScheduleWindow

    if not slug.startswith(CAPTURE_SHOW_PREFIX):
        raise CommandError(f'refusing to schedule against "{slug}" — not a capture show')
    show = Show.objects.get(slug=slug)
    window = ScheduleWindow.objects.filter(show=show, kind=INSTALL).first()
    if window is None:
        raise CommandError('no install window on the capture show')
    ArtistSchedule.objects.update_or_create(
        show=show, artist_id=artist_pk, kind=INSTALL,
        defaults={'window': window, 'scheduled_time': window.start})


# ── how-to-record-artwork-ownership ──────────────────────────────────────────
# The only two-actor flow captured so far: a collector claims a piece, and the artist who
# made it confirms. The script signs in as each in turn by clearing cookies — one guide
# gets one browser context, and both halves of the exchange have to be photographed.

def _cleanup_ownership():
    """Remove the claim the run makes against a seeded artwork, then the account.

    The collector's own CollectionPiece rows go with the account, but the confirmation is
    performed as the seeded artist, so anything left here would accumulate on their
    profile across runs.
    """
    from gallery.models.collection import CollectionPiece

    User = get_user_model()
    CollectionPiece.objects.filter(
        collector__in=User.objects.filter(email__iexact=CAPTURE_EMAIL)).delete()
    _reset_capture_account()


def prepare_record_ownership():
    """A collector account, and a visible artwork by an artist we can also sign in as."""
    from gallery.permissions import visible_artwork_queryset

    _reset_capture_account()
    collector = _create_verified_artist(CAPTURE_EMAIL, complete=True)

    # The artist has to be one with a usable login, since the guide's middle steps are
    # what *they* see. The seeded accounts all share one password.
    artwork = (Artwork.objects
               .filter(visible_artwork_queryset(collector.user))
               .filter(artists__user__isnull=False)
               .exclude(artists__user=collector.user)
               .distinct().order_by('pk').first())
    if artwork is None:
        raise CommandError(
            'No publicly visible artwork belongs to an artist with an account, so nobody '
            'could confirm the claim. Re-seed with `bash scripts/create_test_database.sh`.')
    owner = artwork.artists.filter(user__isnull=False).first()
    return {'artwork_url': artwork.get_absolute_url(),
            'artwork_name': artwork.name,
            'owner_email': owner.user.email,
            'collector_slug': collector.slug}


def capture_record_ownership(rec, facts):
    """Claim, confirm, and what each side sees in between."""
    _log_in(rec)

    # Step 1 — "Navigate to the artwork detail page for a piece you own."
    rec.at_step(1)
    rec.goto(facts['artwork_url'])
    rec.shot(1)

    # Step 2 — "Click 'Claim' in the button bar at the bottom of the artwork card."
    rec.at_step(2)
    rec.shot_region(2, '.card__info:has(button:has-text("Claim"))')
    rec.click('click Claim', rec.page.get_by_role('button', name='Claim'))

    # Step 4 — "While pending, a yellow 'awaiting artist confirmation' badge appears."
    # Out of order on purpose: the badge only exists once the claim has been made, and
    # step 3 is what the *artist* sees, which needs a different login.
    rec.at_step(4)
    rec.goto(facts['artwork_url'])
    # The badge in its card: the step says it appears "on the artwork detail page", and
    # an 18px-tall crop of the badge alone shows the words but not where they are.
    rec.shot_region(4, '.card__content:has(.badge.bg-warning), .badge.bg-warning')

    # Step 3 — "The artwork's artist sees your claim under 'Pending Collection
    #           Confirmations' on their profile page and clicks Confirm or Decline."
    rec.at_step(3)
    rec.sign_out()
    _log_in(rec, facts['owner_email'], SEEDED_PASSWORD)
    rec.click('go to your profile page', rec.control('Me'))
    rec.expect_text('find the "Pending Collection Confirmations" section',
                    'Pending Collection Confirmations')
    rec.shot_region(3, '.section-label:has-text("Pending Collection Confirmations")',
                    '.card:has(button:has-text("Confirm"))')
    rec.click('click Confirm', rec.page.get_by_role('button', name='Confirm'))

    # Step 5 — "Once confirmed, a green badge appears and the piece is listed in your
    #           Collection on your public artist profile."
    rec.at_step(5)
    rec.sign_out()
    _log_in(rec)
    rec.goto(facts['artwork_url'])
    rec.shot_region(5, '.card__content:has(.badge.bg-success), .badge.bg-success')

    # Step 6 is dragging collection cards to reorder — a gesture.

    # Step 7 — "click 'Unclaim' in the button bar on the artwork detail page."
    rec.at_step(7)
    rec.shot_region(7, '.card__info:has(button:has-text("Unclaim"))')


# ── linking-your-account-to-an-existing-artist-profile ───────────────────────

def prepare_link_account():
    """An account whose auto-created profile is still blank, which is what the claim page
    requires — it refuses anyone who already has a profile with real content in it."""
    _reset_capture_account()
    _create_verified_artist(CAPTURE_EMAIL, complete=False)
    return {}


def capture_link_account(rec, facts):
    """Claiming an artist record the gallery made for you under a different address."""
    _log_in(rec)

    # Steps 1 and 2 are working out which of three cases applies, and the case where the
    # link already happened by itself — neither is a screen.

    # Step 3 — "Sign in, then go to your artist edit page — you will see a link to 'link
    #           it to your account here'. Click it, enter the OLD email address ... and
    #           submit."
    rec.at_step(3)
    rec.goto('/accounts/claim-artist/')
    rec.shot_region(3, 'form:has(button:has-text("Claim profile"))')

    # Step 4 — "A new artist profile was created for you automatically ... Go to your
    #           artist profile and fill in your bio, statement, website, Instagram, and
    #           upload a profile photo."
    rec.at_step(4)
    rec.click('go to your artist profile', rec.control('Me'))
    rec.click('open your profile for editing',
              rec.page.locator('a[href*="/edit/"]'))
    rec.shot_region(4, 'fieldset:has(#div_id_bio)')

    # Step 5 is who to email when none of the three cases is obvious.


# ── Generated-PDF guides ─────────────────────────────────────────────────────
# Both guides are mostly about what the PDF contains, not about the page that makes it, so
# these render actual pages with shot_pdf. Captured against the closed Autumn Open, which
# carries the full catalogue — a checklist of four works would not show what the guide
# describes (artists in columns, one entry per piece, a bios section).

PDF_SHOW_SLUG_PREFIX = 'autumn-open'


def _pdf_show():
    show = Show.objects.filter(slug__startswith=PDF_SHOW_SLUG_PREFIX).first()
    if show is None or show.artworks.count() < 5:
        raise CommandError(
            'No closed show with a real catalogue, so the generated PDFs would have '
            'almost nothing in them. Re-seed with `bash scripts/create_test_database.sh`.')
    return show


def _pdf_show_curator(show):
    """Someone who can actually reach the Produce menu on this show.

    Looked up rather than assumed: the closed Autumn Open is curated by a different
    account from the in-review show the jury guides use, and the menu simply is not
    rendered for anyone who cannot manage the show.
    """
    curator = show.curators.filter(user__isnull=False).first()
    if curator is None:
        raise CommandError(
            f'"{show.slug}" has no curator with a login, so nobody can open the Produce '
            f'menu the guide describes. Re-seed.')
    return curator.user.email


def prepare_checklist_pdf():
    show = _pdf_show()
    return {'slug': show.slug, 'pk': show.pk,
            'curator_email': _pdf_show_curator(show)}


def capture_checklist_pdf(rec, facts):
    """The exhibition checklist: where the link is, then the pages it produces."""
    _log_in(rec, facts['curator_email'], SEEDED_PASSWORD)
    show_url = f'/show/{facts["slug"]}/'

    # Step 1 — "On the show detail page, click 'Checklist PDF'." It is in the Produce
    # menu, so the menu has to be open for the reader to see where.
    rec.at_step(1)
    rec.goto(show_url)
    rec.click('open the Produce menu', rec.control('Produce'))
    rec.expect_visible('see Checklist PDF in the menu', '.dropdown-menu.show')
    rec.shot_region(1, '.dropdown-menu.show')

    pdf = f'{show_url}checklist.pdf'

    # Step 2 — "It opens with a cover page: the show title, 'Curated by…', the date range,
    #           ... the list of participating artists ... and the show image."
    rec.shot_pdf(2, pdf, page_number=1)

    # Step 3 — "Then one entry per artwork — a small thumbnail with the artist, title
    #           (year), medium, dimensions, and price — grouped by artist."
    rec.shot_pdf(3, pdf, page_number=2)

    # Step 4 — "It ends with an 'Artists' section — every participating artist's photo
    #           with their name ... then the curator(s)."
    rec.shot_pdf(4, pdf, page_number='last')

    # Step 5 is the per-page footer, which is part of every page above rather than a page
    # of its own, and step 6 says this is not the Avery placard sheet.


def prepare_placards_pdf():
    show = _pdf_show()
    return {'slug': show.slug, 'pk': show.pk,
            'curator_email': _pdf_show_curator(show)}


def capture_placards_pdf(rec, facts):
    """The Avery 5376 card sheet, including the alignment-check variant."""
    _log_in(rec, facts['curator_email'], SEEDED_PASSWORD)
    show_url = f'/show/{facts["slug"]}/'

    # Step 1 — "On the show detail page, click 'Placards PDF'."
    rec.at_step(1)
    rec.goto(show_url)
    rec.click('open the Produce menu', rec.control('Produce'))
    rec.expect_visible('see Placards PDF in the menu', '.dropdown-menu.show')
    rec.shot_region(1, '.dropdown-menu.show')

    sheet = f'{show_url}placard-sheet/'

    # Step 2 — "laid out for Avery 5376 business-card sheets — US Letter, ten 2 x 3.5 inch
    #           cards per page (2 columns x 5 rows)."
    rec.shot_pdf(2, sheet, page_number=1)

    # Step 3 — "Each card shows the title, year(s), artist(s), medium, and dimensions —
    #           no price — plus a QR code." Rendered larger, because the point of this
    #           step is what is printed on one card.
    rec.shot_pdf(3, sheet, page_number=1, css_width=1000)

    # Step 4 is the card ordering and printing at 100%.

    # Step 5 — "add ?outlines=1 to the PDF link ... to draw faint card borders; print it,
    #           hold it against a blank Avery sheet to confirm registration."
    rec.shot_pdf(5, f'{sheet}?outlines=1', page_number=1)


# ── how-to-save-and-restore-layout-snapshots ─────────────────────────────────
# The layout editor itself is a canvas tool and its placement steps are not worth
# capturing as stills, but the snapshot panel is ordinary DOM sitting on top of it, so
# this guide is capturable even though its parent guide is not.

def prepare_layout_snapshots():
    """A show whose layout has snapshots — the panel is empty and says so otherwise."""
    from gallery.models.room import ShowLayoutSnapshot

    _cleanup_capture_shows()
    show = _pdf_show()          # the closed Autumn Open: a real site and real artworks
    curator_email = _pdf_show_curator(show)
    user = get_user_model().objects.filter(email=curator_email).first()

    existing = set(ShowLayoutSnapshot.objects.filter(show=show)
                   .values_list('pk', flat=True))
    payload = {'placements': [], 'supports': [], 'room': {}}
    ShowLayoutSnapshot.objects.create(
        show=show, name='Opening night final',
        kind=ShowLayoutSnapshot.MANUAL, payload=payload, created_by=user)
    ShowLayoutSnapshot.objects.create(
        show=show, name='', kind=ShowLayoutSnapshot.AUTO,
        payload=payload, created_by=user)
    return {'slug': show.slug, 'curator_email': curator_email,
            'baseline': sorted(existing)}


def _cleanup_layout_snapshots():
    """Remove only the snapshots this run added to a seeded show."""
    from gallery.models.room import ShowLayoutSnapshot

    ShowLayoutSnapshot.objects.filter(
        name__in=['Opening night final', ''],
        show__slug__startswith=PDF_SHOW_SLUG_PREFIX).delete()
    _cleanup_capture_shows()


def capture_layout_snapshots(rec, facts):
    """The snapshot panel: saving a restore point, and rolling back to one."""
    _log_in(rec, facts['curator_email'], SEEDED_PASSWORD)
    rec.goto(f'/show/{facts["slug"]}/layout/')

    # Step 1 — "In the room layout editor toolbar, click 'Snapshots'."
    rec.at_step(1)
    rec.expect_visible('see the layout editor toolbar', '#btn-snapshots')
    # The toolbar, so "in the room layout editor toolbar" is visible — the button on its
    # own was 84x27 px and could have been anywhere.
    rec.shot_region(1, '#toolbar')
    rec.click('click Snapshots', rec.page.locator('#btn-snapshots'))
    rec.expect_visible('see the snapshot panel open', '#snapshots-panel.open')

    # Step 2 — "type a name ... and click Save."
    rec.at_step(2)
    rec.fill('name the snapshot', '#snap-name', 'Before rehang')
    rec.shot_region(2, '#snap-name', '#snap-save')

    # Step 3 — "The list shows your saved (green 'saved') and automatic (grey 'auto')
    #           ones, each with when it was taken, who took it, and how many
    #           pieces/supports it holds."
    rec.at_step(3)
    rec.expect_visible('see the list of snapshots', '#snap-list .snap-row')
    rec.shot_region(3, '#snap-list')

    # Step 4 — "click 'Restore' next to a snapshot and confirm."
    rec.at_step(4)
    rec.shot_region(4, '#snap-list .snap-row')

    # Step 5 — "click the trash button on a snapshot row ... Close the panel with the ×."
    rec.at_step(5)
    rec.shot_region(5, '#snapshots-panel')

    # Steps 6-8 are why snapshots exist, the stale-edit guard, and the command-line
    # export/import — none of them a control in this panel.


CAPTURE_SCRIPTS = {
    'submit-artwork': {
        'prepare': prepare_submit_artwork,
        'run': capture_submit_artwork,
        # Steps with no single screen to show: the invitation-email preamble (1), the
        # submission-cap rule (8, which needs a show that sets a cap), and the two
        # after-the-fact outcomes — submissions closing at In Review (11) and the
        # decision email (12). Declared, not skipped: any step neither captured nor
        # listed here is reported as uncovered, so adding a step to a guide surfaces
        # here instead of quietly going un-illustrated.
        'prose_only': {1, 8, 11, 12},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
        # This script walks real signup, and the signup form carries a ReCaptchaField
        # whenever keys are configured (accounts/forms.py). A headless browser cannot
        # solve it, so the guard below turns that into an instruction rather than an
        # inscrutable "the form re-rendered" failure.
        'needs_recaptcha_off': True,
    },
    'how-to-sign-up-for-an-account': {
        'prepare': prepare_sign_up,
        'run': capture_sign_up,
        'prose_only': set(),
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
        'needs_recaptcha_off': True,
    },
    'how-to-complete-your-artist-profile': {
        'prepare': prepare_complete_profile,
        'run': capture_complete_profile,
        # Step 5 is about when a profile becomes publicly visible — a rule, not a screen.
        'prose_only': {5},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'how-to-add-artworks': {
        'prepare': prepare_add_artworks,
        'run': capture_add_artworks,
        # Step 6 is about new work staying private until a show publishes it.
        'prose_only': {6},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'how-to-buy-a-piece-of-artwork': {
        'prepare': prepare_buy_artwork,
        'run': capture_buy_artwork,
        # Steps 6-8 are the email exchange afterwards and pointers to other guides.
        'prose_only': {6, 7, 8},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
        # ArtworkInquiryForm carries a ReCaptchaField, so submitting it headlessly needs
        # the check off. The step 4 screenshot therefore shows the form *without* the
        # "I'm not a robot" widget a real visitor sees — the guide says "may be asked",
        # which covers it.
        'needs_recaptcha_off': True,
    },
    'how-to-adjust-card-sizes': {
        'prepare': prepare_adjust_card_sizes,
        'run': capture_adjust_card_sizes,
        # Step 4 is double-clicking to reset, whose result is step 1's picture again.
        'prose_only': {4},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'how-to-jury-a-show': {
        'prepare': prepare_jury,
        'run': capture_jury_a_show,
        # 1-2 are having an account and being assigned; 8-10 are revisiting scores, how
        # averaging is used, and the curator-who-also-jurors case.
        'prose_only': {1, 2, 8, 9, 10},
        'reset': _reset_capture_account,
        'cleanup': _cleanup_jury,
    },
    'how-to-use-the-review-slideshow': {
        'prepare': prepare_jury,
        'run': capture_review_slideshow,
        # 5, 6, 8, 9, 10 are keyboard gestures and navigation, not screens.
        'prose_only': {5, 6, 8, 9, 10},
        'reset': _reset_capture_account,
        'cleanup': _cleanup_jury,
    },
    'how-to-use-the-curation-slideshow': {
        'prepare': prepare_curation,
        'run': capture_curation_slideshow,
        # 2 is the ordering of the sequence; 9-11 and 13 are keyboard and closing.
        'prose_only': {2, 9, 10, 11, 13},
        'reset': _reset_capture_account,
        'cleanup': _cleanup_jury,
    },
    'how-to-add-artwork-on-behalf-of-an-artist-curatorstaff': {
        'prepare': prepare_add_on_behalf,
        'run': capture_add_on_behalf,
        # 1 is when to use it at all; 7 is linking the profile to an account later.
        'prose_only': {1, 7},
        'reset': _cleanup_capture_shows,
        'cleanup': _cleanup_capture_shows,
    },
    'how-to-run-an-invitation-only-show': {
        'prepare': prepare_invitation_show,
        'run': capture_invitation_show,
        # 5 is the invitation email's contents; 7 points at the on-behalf guide; 8 and 13
        # are further uses of the status control photographed in step 6.
        'prose_only': {5, 7, 8, 13},
        'reset': _cleanup_capture_shows,
        'cleanup': _cleanup_capture_shows,
    },
    'show-lifecycle-and-status': {
        'prepare': prepare_show_lifecycle,
        'run': capture_show_lifecycle,
        # 1 is what a status is for; 9 is who can see which — rules, not screens.
        'prose_only': {1, 9},
        'reset': _cleanup_capture_shows,
        'cleanup': _cleanup_capture_shows,
    },
    'how-to-run-an-open-call-show': {
        'prepare': prepare_open_call_show,
        'run': capture_open_call_show,
        # 6, 8 and 14 are further uses of the status control shown in step 2; 10 points
        # at the curation slideshow guide.
        'prose_only': {6, 8, 10, 14},
        'reset': _cleanup_capture_shows,
        'cleanup': _cleanup_capture_shows,
    },
    'how-to-run-a-public-art-site-open-call': {
        'prepare': prepare_public_art_show,
        'run': capture_public_art_show,
        # 1 is the venue prerequisite (its own guide); 6 says the rest is unchanged.
        'prose_only': {1, 6},
        'reset': _cleanup_capture_shows,
        'cleanup': _cleanup_capture_shows,
    },
    'how-to-create-a-show-staff-only': {
        'prepare': prepare_create_show,
        'run': capture_create_show,
        'prose_only': {9},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'out-of-area-submissions': {
        'prepare': prepare_out_of_area,
        'run': capture_out_of_area,
        # 1 is that the check never blocks; 3 is the command line; 9 repeats 1.
        'prose_only': {1, 3, 9},
        'reset': _cleanup_out_of_area,
        'cleanup': _cleanup_out_of_area,
    },
    'how-to-create-and-manage-sites-staff-only': {
        'prepare': prepare_manage_sites,
        'run': capture_manage_sites,
        # 10 is saving; 12 is deleting, behind the Edit page shown in step 11.
        'prose_only': {10, 12},
        'reset': _cleanup_capture_sites,
        'cleanup': _cleanup_capture_sites,
    },
    'how-to-configure-a-sites-room-and-walls-staff-only': {
        'prepare': prepare_room_config,
        'run': capture_room_config,
        # 6 and 7 are how obstacles render elsewhere and that corner handles are
        # automatic; 9 is saving.
        'prose_only': {6, 7, 9},
        'reset': _cleanup_capture_sites,
        'cleanup': _cleanup_capture_sites,
    },
    'how-to-link-an-artist-profile-to-a-user-staff-only': {
        'prepare': prepare_link_artist,
        'run': capture_link_artist,
        'prose_only': {5},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'how-to-set-up-install-drop-off-and-pickup-times-curator': {
        'prepare': prepare_schedule_windows,
        'run': capture_schedule_windows,
        # 6 is what the artist then sees — the companion guide's subject.
        'prose_only': {6},
        'reset': _cleanup_capture_shows,
        'cleanup': _cleanup_capture_shows,
    },
    'how-to-schedule-your-art-install-drop-off-and-pickup': {
        'prepare': prepare_artist_schedule,
        'run': capture_artist_schedule,
        # 4 is what install vs drop-off means — a rule, not a control.
        'prose_only': {4},
        'reset': _cleanup_capture_shows,
        'cleanup': _cleanup_capture_shows,
    },
    'how-to-record-artwork-ownership': {
        'prepare': prepare_record_ownership,
        'run': capture_record_ownership,
        # 6 is dragging collection cards to reorder — a gesture, not a screen.
        'prose_only': {6},
        'reset': _reset_capture_account,
        'cleanup': _cleanup_ownership,
    },
    'linking-your-account-to-an-existing-artist-profile': {
        'prepare': prepare_link_account,
        'run': capture_link_account,
        # 1 and 2 are working out which case applies and the one that needs no action;
        # 5 is who to email when none of them fits.
        'prose_only': {1, 2, 5},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'how-to-download-a-show-checklist-pdf': {
        'prepare': prepare_checklist_pdf,
        'run': capture_checklist_pdf,
        # 5 is the footer, which appears on every page above; 6 says this is not the
        # Avery sheet.
        'prose_only': {5, 6},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'how-to-print-placards-for-a-show-avery-5376-cards': {
        'prepare': prepare_placards_pdf,
        'run': capture_placards_pdf,
        # 4 is card ordering and printer settings.
        'prose_only': {4},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
    'how-to-save-and-restore-layout-snapshots': {
        'prepare': prepare_layout_snapshots,
        'run': capture_layout_snapshots,
        # 6-8 are why snapshots exist, the stale-edit guard, and the command-line
        # export/import — none is a control in the panel.
        'prose_only': {6, 7, 8},
        'reset': _cleanup_layout_snapshots,
        'cleanup': _cleanup_layout_snapshots,
    },
    'how-to-pin-artworks': {
        'prepare': prepare_pin_artworks,
        'run': capture_pin_artworks,
        # Step 3 is pinning from inside a slideshow (a transient full-screen overlay) and
        # step 5 is drag-to-reorder. Neither reads as a still; they want a short video or
        # stay prose.
        'prose_only': {3, 5},
        'reset': _reset_capture_account,
        'cleanup': _reset_capture_account,
    },
}


# ---------------------------------------------------------------------------
# Placeholder imagery
# ---------------------------------------------------------------------------
# Generated rather than taken from test_fixtures/: those are real works by named
# artists, and a screenshot would show them uploaded under the capture script's fake
# artist name. Misattribution in public help pages is not worth the realism.

def _jpeg(image):
    buf = io.BytesIO()
    image.convert('RGB').save(buf, 'JPEG', quality=85)
    return buf.getvalue()


def _portrait_placeholder():
    """A neutral head-and-shoulders stand-in for the profile photo requirement."""
    from PIL import Image, ImageDraw
    size = 600
    img = Image.new('RGB', (size, size), (222, 226, 230))
    draw = ImageDraw.Draw(img)
    draw.ellipse((size * .34, size * .18, size * .66, size * .50), fill=(158, 168, 178))
    draw.ellipse((size * .18, size * .56, size * .82, size * 1.20), fill=(158, 168, 178))
    return _jpeg(img)


def _artwork_placeholder():
    """An abstract composition — reads as "a painting" at screenshot scale."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = 800, 1200
    img = Image.new('RGB', (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)],
                  fill=(int(94 + 90 * t), int(122 + 70 * t), int(104 + 60 * t)))
    draw.ellipse((w * .12, h * .18, w * .74, h * .52), fill=(232, 226, 205))
    draw.rectangle((w * .30, h * .55, w * .88, h * .80), fill=(120, 92, 78))
    return _jpeg(img.filter(ImageFilter.GaussianBlur(6)))


# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = ('Regenerate the per-step screenshots for a how-to guide by driving a '
            'browser through it (local development only).')

    def add_arguments(self, parser):
        parser.add_argument('image_key', nargs='?',
                            help='Which guide to capture (see --list).')
        parser.add_argument('--list', action='store_true',
                            help='List the guides that have a capture script.')
        parser.add_argument('--all', action='store_true',
                            help='Regenerate every guide that has a capture script. '
                                 'Reuses one browser and one server for the batch, and '
                                 'reports every failure at the end rather than stopping '
                                 'at the first.')
        parser.add_argument('--publish', action='store_true',
                            help='Upload locally captured images to S3 and update the '
                                 'manifest. Does not re-capture; run the capture first, '
                                 'look at the result, then publish. With no image_key, '
                                 'publishes every guide captured in this working copy '
                                 'whose images are not already on S3.')
        parser.add_argument('--dry-run', action='store_true',
                            help='With --publish: report the object keys that would be '
                                 'written, and upload nothing.')
        parser.add_argument('--prune', action='store_true',
                            help='List objects under the howto/ prefix that no manifest '
                                 'entry references, and delete them with --yes. Every '
                                 'regeneration leaves the previous versions behind, so '
                                 'these accumulate.')
        parser.add_argument('--yes', action='store_true',
                            help='With --prune: actually delete. Without it, --prune only '
                                 'reports.')
        parser.add_argument('--force', action='store_true',
                            help='With --publish: re-upload even guides whose staged '
                                 'images are already on S3 unchanged.')
        parser.add_argument('--keep', action='store_true',
                            help='Leave the throwaway account and its work in the '
                                 'database, to inspect what the run produced.')
        parser.add_argument('--headed', action='store_true',
                            help='Show the browser instead of running headless.')
        # Narrower than a typical desktop browser on purpose: the help page shows
        # these at 1:1, so a capture wider than the reader's column gets shrunk to fit
        # and the labels stop being readable.
        parser.add_argument('--width', type=int, default=1100)
        parser.add_argument('--height', type=int, default=900)

    def handle(self, *args, **opts):
        if opts['list']:
            self._list()
            return

        if opts['prune']:
            self._prune(delete=opts['yes'])
            return

        if opts['publish']:
            if opts['image_key']:
                keys = [opts['image_key']]
            else:
                # Everything staged. Deliberately not "everything in HOW_TO_GUIDES":
                # staging is gitignored, so this can only ever mean guides captured here.
                keys = self._staged_keys()
                if not keys:
                    raise CommandError(
                        'Nothing is captured locally, so there is nothing to publish.\n'
                        '    Capture a guide first: manage.py capture_howto <image_key>\n'
                        '    (staged images live in static/img/howto/ and are gitignored, '
                        'so a fresh checkout starts empty.)')
                self.stdout.write(f'Found {len(keys)} guide(s) captured locally.')
            self._publish(keys, dry_run=opts['dry_run'], force=opts['force'])
            return

        # ── capture ──
        if opts['all'] and opts['image_key']:
            raise CommandError('Pass either an image key or --all, not both.')
        if opts['all']:
            keys = [image_key(g) for g in HOW_TO_GUIDES
                    if image_key(g) in CAPTURE_SCRIPTS]
        elif opts['image_key']:
            keys = [opts['image_key']]
        else:
            raise CommandError(
                'Which guide? Pass an image key, --all to regenerate every guide that '
                'has a script, or --list to see them.')

        unknown = [k for k in keys if k not in CAPTURE_SCRIPTS]
        if unknown:
            raise CommandError(
                f'No capture script for {unknown}. Run --list to see what there is.')

        if not getattr(settings, 'LOCAL_DEV', False):
            raise CommandError(
                'capture_howto only runs in local development (settings.LOCAL_DEV). It '
                'creates an account, uploads files and submits work, and curator pages '
                'expose artist emails and phone numbers — never point it at a '
                'deployment.')

        # Checked for the whole batch up front: finding out at guide 4 of 7 that the
        # reCAPTCHA is on would waste the three that already ran.
        needs_off = [k for k in keys
                     if CAPTURE_SCRIPTS[k].get('needs_recaptcha_off')]
        if needs_off and getattr(settings, 'RECAPTCHA_ENABLED', False):
            raise CommandError(
                f'{needs_off} walk forms carrying a reCAPTCHA, which a headless browser '
                f'cannot solve. Re-run with it switched off:\n'
                f'    RECAPTCHA_ENABLED=false ./env/bin/python manage.py capture_howto '
                + ('--all' if opts['all'] else keys[0]))

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError(
                'Playwright is not installed. Run:\n'
                '    ./env/bin/pip install playwright\n'
                '    ./env/bin/playwright install chromium')

        # One server and one browser for the whole batch — starting them per guide is most
        # of the wall-clock cost of a full regeneration. Each guide still gets a fresh
        # browser context, so no session, cookie or localStorage state leaks between them.
        server = LiveServerThread('localhost', StaticFilesHandler, port=0)
        server.daemon = True
        server.start()
        server.is_ready.wait()
        if server.error:
            raise CommandError(f'Could not start the capture server: {server.error}')
        base_url = f'http://localhost:{server.port}'

        results = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not opts['headed'])
                try:
                    for key in keys:
                        results.append(
                            self._capture_one(key, browser, base_url, opts))
                finally:
                    browser.close()
        finally:
            server.terminate()

        self._report_batch(results, keys)

    def _capture_one(self, key, browser, base_url, opts):
        """Capture one guide. Returns a result dict; never raises for a capture failure.

        A failure in guide 3 of 7 must not throw away the other six — a batch run reports
        every problem at the end instead, which is also more useful when several guides
        have drifted from their prose at once. `handle` still exits non-zero.
        """
        guide = self._guide_for(key)
        script = CAPTURE_SCRIPTS[key]
        out_dir = staging_dir(key)

        # Wiped, not merged: a guide that lost a step would otherwise keep the orphaned
        # image and caption the wrong prose with it.
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nCapturing "{guide["title"]}"'))
        self.stdout.write(f'  {len(guide["steps"])} steps → {out_dir}')

        rec = None
        try:
            # Through _db(): a batch run holds the Playwright event loop open across every
            # guide, so even this per-guide setup is inside an async context as far as
            # Django is concerned. Reset and prepare must still run per guide rather than
            # all up front — each one creates an account at the same CAPTURE_EMAIL.
            _db(script['reset'])
            facts = _db(script['prepare'])
            context = browser.new_context(
                viewport={'width': opts['width'], 'height': opts['height']},
                # Rendered at 1:1 on the help page, so this is spare resolution for
                # retina rather than something that gets scaled away. Shared with
                # howto_images so the two cannot drift.
                device_scale_factor=HOWTO_CAPTURE_SCALE,
            )
            try:
                rec = Recorder(context.new_page(), base_url, out_dir, self.stdout.write)
                script['run'](rec, facts)
            finally:
                context.close()
        except Exception as exc:
            # Deliberately broad. A script can fail in ways the Recorder does not wrap —
            # a bare Playwright timeout, a selector typo — and in a batch those must not
            # discard the guides that already succeeded or skip the summary. The type is
            # included so a genuine programming error is still recognisable as one.
            label = (str(exc) if isinstance(exc, CommandError)
                     else f'{type(exc).__name__}: {exc}')
            self.stdout.write(self.style.ERROR(f'  {label}'))
            return {'key': key, 'guide': guide, 'script': script, 'rec': rec,
                    'error': label}
        finally:
            if opts['keep']:
                self.stdout.write(f'  --keep: left {CAPTURE_EMAIL} in the database.')
            else:
                _db(script['cleanup'])

        self._report(guide, script, rec)
        return {'key': key, 'guide': guide, 'script': script, 'rec': rec, 'error': None}

    def _report_batch(self, results, keys):
        """One summary for the whole run, and a non-zero exit if anything failed."""
        failed = [r for r in results if r['error']]
        if len(results) > 1:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n{len(results) - len(failed)} of {len(results)} guides captured'))
            for r in results:
                if r['error']:
                    self.stdout.write(self.style.ERROR(f'  failed   {r["key"]}'))
                else:
                    n = len(r['rec'].captured) if r['rec'] else 0
                    self.stdout.write(f'  ok       {r["key"]}  ({n} images)')
            self.stdout.write(
                '\nPublish what changed:\n'
                '    ./env/bin/python manage.py capture_howto --publish')
        if failed:
            raise CommandError(
                f'{len(failed)} guide(s) failed: {[r["key"] for r in failed]}. '
                f'A DocumentationMismatch means that guide\'s prose is now wrong too — '
                f'fix the wording in eatart/role_docs.py, then re-run.')

    def _guide_for(self, key):
        for guide in HOW_TO_GUIDES:
            if image_key(guide) == key:
                return guide
        raise CommandError(
            f'"{key}" has a capture script but no guide in HOW_TO_GUIDES uses that '
            f'image key. Was the guide renamed or removed?')

    def _list(self):
        self.stdout.write('Guides with a capture script:')
        for key, script in sorted(CAPTURE_SCRIPTS.items()):
            guide = next((g for g in HOW_TO_GUIDES if image_key(g) == key), None)
            title = guide['title'] if guide else self.style.ERROR('(no such guide)')
            self.stdout.write(f'  {key:<28} {title}')
        uncovered = [image_key(g) for g in HOW_TO_GUIDES
                     if image_key(g) not in CAPTURE_SCRIPTS]
        self.stdout.write(f'\n{len(uncovered)} guides have no script yet.')

    def _report(self, guide, script, rec):
        total = len(guide['steps'])
        expected = set(range(1, total + 1)) - script['prose_only']
        missing = sorted(expected - rec.captured)
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Captured {len(rec.captured)} of {len(expected)} illustratable steps '
            f'({len(script["prose_only"])} prose-only).'))
        if missing:
            self.stdout.write(self.style.WARNING(
                f'Steps with no screenshot: {missing}. Either extend the script in '
                f'gallery/management/commands/capture_howto.py, or add them to '
                f'"prose_only" if they have no single screen to show.'))
        self.stdout.write(
            f'\nLook at it locally first: /howto/{guide.get("anchor") or ""}/ '
            f'(DEBUG prefers the staged files over what is published).\n'
            f'Then publish:\n'
            f'    ./env/bin/python manage.py capture_howto {image_key(guide)} --publish')

    # -- publishing -------------------------------------------------------------

    def _prune(self, delete=False):
        """Report (and optionally delete) published objects nothing references.

        Names are content-hashed, so every regeneration of a changed screenshot writes a
        new object and abandons the old one — 149 live images had left 64 dead ones
        behind. They are harmless but they are not free, and nothing else will ever
        collect them.

        Reports by default. Deleting is irreversible and a stale local manifest would
        make it delete objects a deployed branch still points at, so --yes is required.
        """
        import json
        import subprocess

        import boto3

        bucket = getattr(settings, 'HOWTO_IMAGE_BUCKET', None)
        if not bucket:
            raise CommandError('No bucket configured — set AWS_STORAGE_BUCKET_NAME.')

        prefix = f'{settings.HOWTO_IMAGE_LOCATION}/'
        client = boto3.client('s3', region_name=settings.HOWTO_IMAGE_REGION)
        found = {}
        token = None
        while True:
            kwargs = {'Bucket': bucket, 'Prefix': prefix}
            if token:
                kwargs['ContinuationToken'] = token
            page = client.list_objects_v2(**kwargs)
            for obj in page.get('Contents', []):
                found[obj['Key']] = obj['Size']
            if not page.get('IsTruncated'):
                break
            token = page['NextContinuationToken']

        def keys_of(manifest):
            return {prefix + entry['key']
                    for guide in manifest.values() for entry in guide.values()
                    if entry.get('key')}

        live = keys_of(load_manifest())
        # Also spare anything the *pushed* manifest still points at. The working copy is
        # routinely ahead of what is deployed, and every commit that regenerates a guide
        # abandons the previous objects — which the deployed help pages are still serving.
        # Pruning against the local manifest alone deleted 13 live images in a dry run.
        for ref in ('origin/main', 'HEAD'):
            try:
                out = subprocess.run(
                    ['git', 'show', f'{ref}:eatart/howto_manifest.json'],
                    capture_output=True, text=True, check=True).stdout
                live |= keys_of(json.loads(out))
            except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
                self.stdout.write(self.style.WARNING(
                    f'  (could not read the manifest at {ref}; its images are not '
                    f'protected)'))
        if not live:
            raise CommandError(
                'The manifest lists no images, so every object here would look orphaned. '
                'Refusing to prune against an empty manifest.')

        orphans = sorted(k for k in found if k not in live)
        total = sum(found[k] for k in orphans)
        self.stdout.write(
            f'{len(found)} objects under {prefix}, {len(live)} referenced by the '
            f'manifest.')
        if not orphans:
            self.stdout.write(self.style.SUCCESS('Nothing to prune.'))
            return
        for key in orphans[:20]:
            self.stdout.write(f'  {key}  {found[key] // 1024} KB')
        if len(orphans) > 20:
            self.stdout.write(f'  … and {len(orphans) - 20} more')
        self.stdout.write(
            f'  ── {len(orphans)} orphaned objects, {total / 1024 / 1024:.1f} MB')

        if not delete:
            self.stdout.write(self.style.WARNING(
                '\nReporting only. Re-run with --prune --yes to delete.'))
            return

        for start in range(0, len(orphans), 1000):
            client.delete_objects(
                Bucket=bucket,
                Delete={'Objects': [{'Key': k} for k in orphans[start:start + 1000]]})
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {len(orphans)} objects ({total / 1024 / 1024:.1f} MB).'))

    def _staged_keys(self):
        """Image keys with locally captured images, in HOW_TO_GUIDES order.

        Staging is gitignored, so this only ever finds guides captured in *this* working
        copy — there is no way to publish something you have not just generated.
        """
        keys = []
        for guide in HOW_TO_GUIDES:
            key = image_key(guide)
            directory = staging_dir(key)
            if directory.is_dir() and any(directory.glob('[0-9][0-9].webp')):
                keys.append(key)
        return keys

    def _plan_publish(self, key, manifest):
        """Work out what publishing `key` would upload, without uploading anything.

        Content-hashed names make change detection free: if every staged image hashes to
        an object key the manifest already records, the guide is already published and
        re-uploading it would be pure noise.
        """
        from PIL import Image

        guide = self._guide_for(key)
        directory = staging_dir(key)
        staged = sorted(directory.glob('[0-9][0-9].webp'))
        if not staged:
            raise CommandError(
                f'Nothing captured for "{key}" — {directory} has no images.\n'
                f'    Run the capture first: manage.py capture_howto {key}')

        overrun = [p.name for p in staged if int(p.stem) > len(guide['steps'])]
        if overrun:
            raise CommandError(
                f'{key}: {overrun} are numbered past the guide\'s '
                f'{len(guide["steps"])} steps, so the guide changed since the capture.\n'
                f'    Re-run: manage.py capture_howto {key}')

        entries, uploads = {}, []
        for path in staged:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()[:12]
            object_key = f'{key}/{path.stem}.{digest}.webp'
            with Image.open(io.BytesIO(raw)) as img:
                width, height = img.size
            entries[str(int(path.stem))] = {
                'key': object_key,
                # Stored in CSS pixels — the size the region had in the browser. The
                # help page renders at exactly this, which is what keeps it legible.
                'width': max(1, round(width / HOWTO_CAPTURE_SCALE)),
                'height': max(1, round(height / HOWTO_CAPTURE_SCALE)),
                'bytes': len(raw),
            }
            uploads.append((object_key, raw))

        published = manifest.get(key, {})
        unchanged = (
            {n: e.get('key') for n, e in published.items()} ==
            {n: e['key'] for n, e in entries.items()})
        return {
            'key': key, 'title': guide['title'], 'entries': entries,
            'uploads': uploads, 'unchanged': unchanged,
            'superseded': {e.get('key') for e in published.values() if e.get('key')}
                          - {k for k, _ in uploads},
        }

    def _publish(self, keys, dry_run=False, force=False):
        """Upload staged captures for one or more guides and rewrite the manifest once.

        Split from capturing on purpose: a misframed screenshot should cost nothing, and
        capture must keep working with no network and no credentials. Chain them freely.

        Object names are content-hashed. That is what lets the bucket's
        `immutable, max-age=1y` cache headers apply to something we regenerate — a
        changed screenshot is a new key, so no cache anywhere ever holds a stale one, and
        an unchanged one needs no upload at all.

        The manifest is written once at the end rather than per guide, so a bulk publish
        is one reviewable diff and one commit.
        """
        if not getattr(settings, 'HOWTO_IMAGE_BUCKET', None):
            raise CommandError(
                'No bucket configured — set AWS_STORAGE_BUCKET_NAME (plus '
                'AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) to publish.')

        manifest = dict(load_manifest())
        plans = [self._plan_publish(key, manifest) for key in keys]
        todo = [p for p in plans if force or not p['unchanged']]
        skipped = [p for p in plans if p not in todo]

        for plan in skipped:
            self.stdout.write(f'  up to date  {plan["key"]} '
                              f'({len(plan["uploads"])} images)')

        if not todo:
            self.stdout.write(self.style.SUCCESS(
                '\nNothing to publish — every staged image is already on S3. '
                'Pass --force to re-upload anyway.'))
            return

        total = sum(len(raw) for p in todo for _, raw in p['uploads'])
        count = sum(len(p['uploads']) for p in todo)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{"Would publish" if dry_run else "Publishing"} {count} images across '
            f'{len(todo)} guide{"" if len(todo) == 1 else "s"} '
            f'({total // 1024} KB) to {settings.HOWTO_IMAGE_BUCKET}'))
        for plan in todo:
            self.stdout.write(f'  {plan["key"]}  '
                              f'({len(plan["uploads"])} images, '
                              f'{sum(len(r) for _, r in plan["uploads"]) // 1024} KB)'
                              + ('  [re-upload]' if plan['unchanged'] else ''))
            for object_key, raw in plan['uploads']:
                self.stdout.write(f'      {settings.HOWTO_IMAGE_LOCATION}/{object_key}  '
                                  f'{len(raw) // 1024} KB')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n--dry-run: nothing uploaded, manifest untouched.'))
            return

        from django.core.files.base import ContentFile

        from eatart.custom_storage import HowtoImageStorage

        storage = HowtoImageStorage()
        for plan in todo:
            for object_key, raw in plan['uploads']:
                storage.save(object_key, ContentFile(raw))

        # Manifest last: an upload with no manifest entry is invisible and harmless,
        # whereas a manifest entry with no object is a broken image on the help page.
        for plan in plans:
            manifest[plan['key']] = plan['entries']

        # Drop entries for image keys no longer used by any guide. Merging or renaming a
        # guide otherwise leaves its old key behind, which fails HowToImageKeyTests and
        # would go on describing screenshots for prose that no longer exists.
        live = {image_key(g) for g in HOW_TO_GUIDES}
        orphaned_keys = [k for k in manifest if k not in live]
        for k in orphaned_keys:
            del manifest[k]

        save_manifest(manifest)

        self.stdout.write(self.style.SUCCESS(
            '\nPublished. Commit eatart/howto_manifest.json to make it live.'))
        first = todo[0]['uploads'][0][0]
        self.stdout.write(f'  {settings.HOWTO_IMAGE_BASE_URL}{first}')
        superseded = sum(len(p['superseded']) for p in todo)
        if superseded:
            # Not deleted: they may still be referenced by a manifest on a deployed
            # branch, and their URLs are cached as immutable for a year.
            self.stdout.write(self.style.WARNING(
                f'  {superseded} previous object(s) are now unreferenced and left in '
                f'the bucket.'))
        if orphaned_keys:
            self.stdout.write(self.style.WARNING(
                f'  dropped manifest entries for {orphaned_keys} — no guide uses those '
                f'image keys any more. Their S3 objects are also left in the bucket.'))
