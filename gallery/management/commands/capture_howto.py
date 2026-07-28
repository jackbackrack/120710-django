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
import shutil
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management.base import BaseCommand, CommandError
from django.test.testcases import LiveServerThread

from eatart.howto_images import (HOWTO_CAPTURE_SCALE, image_key, load_manifest,
                                 save_manifest, staging_dir, step_filename)
from eatart.role_docs import HOW_TO_GUIDES
from gallery.models import Artist, Artwork, Show

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
        if self.page.url != before:
            return
        # Crispy marks the offending widgets, which names the fields; the message text
        # alone is often just a bare "This field is required."
        fields = self.page.locator('.is-invalid, [aria-invalid="true"]').evaluate_all(
            'els => els.map(e => e.getAttribute("name")).filter(Boolean)')
        messages = [t.strip().replace('\n', ' ') for t in self.page.locator(
            '.errorlist, .invalid-feedback, .alert-danger'
        ).all_inner_texts() if t.strip()]
        detail = []
        if fields:
            detail.append(f'rejected fields: {sorted(set(fields))}')
        detail.extend(messages)
        raise CommandError(
            f'step {self.step}: the form on {before} was rejected and re-rendered.\n'
            + ('    ' + '\n    '.join(detail) if detail
               else '    No error text found on the page — re-run with --headed.'))

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

    def select(self, phrasing, selector, value):
        from playwright.sync_api import Error as PlaywrightError
        try:
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

    def expect_text(self, phrasing, text):
        """Assert the reader would see `text` — used for the outcomes steps promise."""
        from playwright.sync_api import Error as PlaywrightError
        try:
            self.page.get_by_text(text, exact=False).first.wait_for(
                state='visible', timeout=self.timeout)
        except PlaywrightError as exc:
            self._mismatch(phrasing, exc)

    # -- capture ----------------------------------------------------------------

    def shot(self, number, selector=None):
        """Write NN.webp for step `number`.

        `selector` crops to one element, and you almost always want it. The help page
        renders these at 1:1, so extent is legibility: a whole-viewport shot of a 2000px
        form either overflows the column or gets shrunk until the labels are mush. Crop
        to the region the step is actually about.
        """
        self.at_step(number)
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
        boxes = []
        for selector in selectors:
            locator = self.page.locator(selector).first
            if not locator.count():
                continue
            box = locator.bounding_box()
            if box and box['width'] and box['height']:
                boxes.append(box)
        if not boxes:
            raise DocumentationMismatch(
                f'step {self.step}: none of {list(selectors)} are on {self.page.url}, '
                f'so there is no region to show.\n'
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

    Playwright's sync API drives the browser from an event loop, so Django treats the
    whole script as an async context and raises SynchronousOnlyOperation on any query. A
    worker thread gets its own context — the escape hatch that error message recommends.

    Use `prepare` for anything knowable up front; this is for facts that do not exist
    until the browser has done something, like an email-confirmation key.
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
    rec.shot_region(4, '#div_id_framed_width_inches', '#div_id_framed_depth_inches')

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
        parser.add_argument('--publish', action='store_true',
                            help='Upload locally captured images to S3 and update the '
                                 'manifest. Does not re-capture; run the capture first, '
                                 'look at the result, then publish. With no image_key, '
                                 'publishes every guide captured in this working copy '
                                 'whose images are not already on S3.')
        parser.add_argument('--dry-run', action='store_true',
                            help='With --publish: report the object keys that would be '
                                 'written, and upload nothing.')
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
                self.stdout.write(
                    f'Found {len(keys)} guide(s) captured locally.')
            self._publish(keys, dry_run=opts['dry_run'], force=opts['force'])
            return

        key = opts['image_key']
        if not key:
            raise CommandError('Which guide? Pass an image key, or --list to see them.')
        # Only capturing needs a script; publishing works off whatever is staged, so a
        # hand-made or hand-edited image directory can still be published.
        if key not in CAPTURE_SCRIPTS:
            raise CommandError(
                f'No capture script for "{key}". Run --list to see what there is.')

        if not getattr(settings, 'LOCAL_DEV', False):
            raise CommandError(
                'capture_howto only runs in local development (settings.LOCAL_DEV). It '
                'creates an account, uploads files and submits work, and curator pages '
                'expose artist emails and phone numbers — never point it at a '
                'deployment.')

        script = CAPTURE_SCRIPTS[key]
        if script.get('needs_recaptcha_off') and getattr(
                settings, 'RECAPTCHA_ENABLED', False):
            raise CommandError(
                'This guide walks the real signup form, which carries a reCAPTCHA while '
                'keys are configured — and a headless browser cannot solve one. Re-run '
                'with it switched off:\n'
                f'    RECAPTCHA_ENABLED=false ./env/bin/python manage.py capture_howto '
                f'{key}')

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError(
                'Playwright is not installed. Run:\n'
                '    ./env/bin/pip install playwright\n'
                '    ./env/bin/playwright install chromium')

        guide = self._guide_for(key)
        out_dir = staging_dir(key)

        # Wiped, not merged: a guide that lost a step would otherwise keep the orphaned
        # image and caption the wrong prose with it.
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Capturing "{guide["title"]}"'))
        self.stdout.write(f'  {len(guide["steps"])} steps → {out_dir}')

        script['reset']()
        # Before the browser exists: see prepare_submit_artwork's docstring.
        facts = script['prepare']()

        server = LiveServerThread('localhost', StaticFilesHandler, port=0)
        server.daemon = True
        server.start()
        server.is_ready.wait()
        if server.error:
            raise CommandError(f'Could not start the capture server: {server.error}')
        base_url = f'http://localhost:{server.port}'

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not opts['headed'])
                context = browser.new_context(
                    viewport={'width': opts['width'], 'height': opts['height']},
                    # Rendered at 1:1 on the help page, so this is spare resolution
                    # for retina rather than something that gets scaled away. Shared
                    # with howto_images so the two cannot drift.
                    device_scale_factor=HOWTO_CAPTURE_SCALE,
                )
                page = context.new_page()
                rec = Recorder(page, base_url, out_dir, self.stdout.write)
                try:
                    script['run'](rec, facts)
                finally:
                    context.close()
                    browser.close()
        finally:
            server.terminate()
            if not opts['keep']:
                script['cleanup']()
            else:
                self.stdout.write(
                    f'  --keep: left {CAPTURE_EMAIL} in the database.')

        self._report(guide, script, rec)

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
