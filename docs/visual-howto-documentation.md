# Visual how-to documentation

**Status: 12 of 30 guides captured** — 54 screenshots, ~4.3 MB on S3, ~7 KB of manifest
in git. The whole artist path is illustrated (sign up → complete profile → add artworks →
submit, that last one for every reader signed in or not), plus pinning, buying and the
card-size control, and now the jury cluster: jurying a show, the review slideshow and the
curation slideshow, plus adding artwork on behalf of an artist and running an
invitation-only show end to end. 18 guides still need a capture script each. Plan agreed
2026-07-28.

**Guides that mutate shared state must build their own.** The lifecycle guides drive a
show through its statuses, invite artists and add work; doing that to a seeded show would
change what every other capture sees. `_create_capture_show()` makes one per run and
`_cleanup_capture_shows()` deletes it. Note the prefix has to be in the show's **name**:
`Show.save()` regenerates the slug from the name, so a slug set at creation is discarded
and a `slug__startswith` cleanup silently matches nothing — that left seven orphaned shows
before it was spotted. After adding such a script, check the DB is unchanged after `--all`.

## What Jack wants

The written how-to guides in `eatart/role_docs.py` should be accompanied by
screenshots — one after each step — so a reader sees the actual UI as they follow
along. Generated **once, automatically**, and then **kept up to date** as the UI
changes, in the same way an AI keeps written documentation current.

## The framing that matters

Don't try to *maintain* screenshots. Make them cheap to **regenerate**, then always
regenerate.

Written docs need AI maintenance because prose encodes judgement and can't be
recomputed from the app. Screenshots can. They are closer to compiled assets than to
documents, so "keeping them current" should be a build step, not an ongoing chore.

The corollary, which is the real payoff: **when a capture step can't find what the
guide told it to click, the written guide is wrong too.** One mechanism keeps the
words and the pictures honest together. Treat a failed capture as a documentation bug,
not just a broken screenshot.

This paid for itself on the first run — see [What the first capture
found](#what-the-first-capture-found).

## How to run it

```
./env/bin/pip install playwright && ./env/bin/playwright install chromium

# 1. capture locally — no network, no AWS credentials needed
RECAPTCHA_ENABLED=false ./env/bin/python manage.py capture_howto submit-artwork
RECAPTCHA_ENABLED=false ./env/bin/python manage.py capture_howto --all   # regenerate everything
# 2. look at it on /howto/<anchor>/ (DEBUG prefers the local staging copies), then publish
./env/bin/python manage.py capture_howto submit-artwork --publish --dry-run
./env/bin/python manage.py capture_howto submit-artwork --publish

# ...or publish every guide captured locally that is not already on S3
./env/bin/python manage.py capture_howto --publish --dry-run
./env/bin/python manage.py capture_howto --publish

./env/bin/python manage.py capture_howto --list      # what has a script, what doesn't
```

Capture and publish are separate on purpose: a misframed screenshot should cost nothing,
and capture has to keep working offline. Chain them freely once you trust a script.

**`--all` regenerates every guide that has a capture script** — 7 guides in ~35 seconds,
reusing one server and one browser for the batch, with a fresh browser context per guide so
no session state leaks. A guide that fails does **not** abort the run: every failure is
reported in the closing summary and the command exits non-zero, because a batch is exactly
when several guides have drifted from their prose at once and you want the whole list.

**Captures are byte-reproducible**, which is what makes `--publish` able to skip unchanged
guides. Two things had to be fixed to get there, and both are easy to reintroduce:

- Every shot blurs the focused element first (`Recorder._settle`). A focused input draws a
  caret that blinks on a timer, so steps ending in a filled field were never identical
  twice — and a caret sitting mid-field reads as a typo in documentation anyway.
- Prefer one stable container over a computed union of several elements. `shot_region` on
  two columns of a crispy row tracked whichever column happened to be tallest, varying by
  2px between runs; `shot` on the row itself is stable.

Without this, every `--all` republished all 35 images and orphaned the previous ones in the
bucket.

**`--publish` with no image key publishes everything staged**, skipping guides whose
images are already on S3 byte-for-byte — content hashing makes that comparison free, so a
bulk publish after changing one guide uploads only that guide. `--force` re-uploads
anyway. The manifest is written once at the end, so a bulk publish is one reviewable diff
and one commit. Staging is gitignored, so this can only ever mean "guides captured in this
working copy" — a fresh checkout has nothing to publish.

- **Playwright is deliberately not in `requirements.txt`.** Capture is local-only and
  refuses to run outside `settings.LOCAL_DEV`, so there is no reason to ship a browser
  driver to production. The command prints both install lines if it is missing.
- **`RECAPTCHA_ENABLED=false` is required** for any guide that walks signup: the signup
  form carries a `ReCaptchaField` whenever keys are configured, and a headless browser
  cannot solve one. Scripts declare `needs_recaptcha_off` and the command tells you.
- **A seeded database is required.** `scripts/create_test_database.sh` — note it **wipes
  `db.sqlite3`**, so back it up first. The script finds an open-call show that is
  actually accepting submissions rather than hard-coding a slug, so a stale database
  fails with an explanation rather than a confusing mismatch.
- The run writes to the database, then cleans up after itself. `--keep` leaves the
  throwaway account in place to inspect. `--headed` shows the browser.

## How it fits together

**Images live on S3, not in git.** They are ~110 KB each and regenerated wholesale, so
full coverage would be tens of megabytes of churning binaries. Only
`eatart/howto_manifest.json` is committed — about 150 bytes per image, recording the
object key and dimensions.

- **Own top-level `howto/` prefix**, not `static/` (which `collectstatic --clear` would
  wipe on deploy) and not `media/` (user uploads).
- **`eatart/custom_storage.py::HowtoImageStorage`**, deliberately independent of the
  `USE_S3_STATIC` / `USE_S3_MEDIA` switches. Publishing has to work from a local checkout
  — the only place a capture can run — without redirecting the rest of local media into
  the bucket, which would push the capture's own throwaway profile photo and placeholder
  artwork there permanently.
- **Content-hashed names** (`06.d193940d24b7.webp`), which is what makes the bucket's
  `immutable, max-age=1y` cache headers safe for something we regenerate: a changed
  screenshot is a new key, so no cache ever holds a stale one. Note the storage class
  sets those headers itself — `AWS_S3_OBJECT_PARAMETERS` in settings only exists inside
  the `if USE_S3_STATIC or USE_S3_MEDIA` block, so without that the objects came back
  with no `Cache-Control` at all.
- **Serving needs only `settings.HOWTO_IMAGE_BASE_URL`** — no boto3, no credentials on
  the web servers. Write access is needed solely by `--publish`, run by hand.
- **Superseded objects are reported, not deleted.** A deployed branch may still reference
  them via its own manifest, and their URLs are cached as immutable for a year.

**One page per guide.** `/howto/` is an index of links and one-line descriptions grouped
by audience; each guide is at `/howto/<anchor>/`, and the form-and-field tables are at
`/howto/reference/`. Split up on 2026-07-28 because illustrating all 31 guides would have
put a few hundred screenshots on one URL — the staff view of the old combined page was
already 160 KB of HTML before any images, and is now 24 KB.

Two things follow that are easy to trip over:

- **Every guide needs a `summary`**, since the index shows only the title and that line.
  A test fails on a missing one.
- **A guide's anchor is now its URL**, so retitling a guide without a stable `'anchor'`
  key changes the URL. Link with `{% url 'howto_guide' 'the-anchor' %}`; the old
  `{% url 'howto' %}#anchor` form now fails the test suite, because guides are no longer
  sections on the index and such a link silently lands the reader on a list.
  `/howto/` carries a small script that forwards an old `#anchor` URL to the right page,
  for links already out in the world — a fragment is never sent to the server, so this
  cannot be a redirect.

**`eatart/howto_images.py`** — the contract between capture and rendering. Nothing about
images is recorded in `role_docs.py`. `steps_with_images()` pairs each step's prose with
its image, leaving steps text-only where nothing was published, so a partially captured
guide is fine.

**Local staging.** Capture also writes plain unhashed files to
`static/img/howto/<key>/NN.webp`, which is gitignored. In DEBUG those take precedence
over the manifest, so a capture is visible on the next reload without publishing.
Production never looks at them.

**Image keys, not titles.** In order: `guide['image_key']`, else `guide['anchor']`, else
`slugify(guide['title'])`. Retitling must not orphan images, the same reason stable
anchors exist for `#howto` links. `image_key` exists because *anchors need not be
unique*: a `public_only` guide and its role-gated counterpart may share one anchor so a
single link serves either reader, and since their steps differ, step 3 of one is not step
3 of the other — sharing an image key would caption one with the other's screenshots.

The submit guide used to be exactly that pair, and it is the cautionary tale (2026-07-28):
only the public half was ever captured, so a signed-in artist following the show page's
link got the un-illustrated version of the flow the app itself walks them through. It is
now **one guide for everyone**, keyed simply on its anchor. Prefer that. `HowToImageKeyTests`
fails if two guides share an image key or if the manifest describes steps a guide no
longer has; `HowToAnchorTests` fails if any one reader can see two guides at the same
anchor.

**Legibility is a sizing problem, not a resolution one.** Captures are taken at a device
scale factor of 2 and the help page renders them at explicit `width`/`height` equal to
the CSS size the region had in the browser — so a screenshot is always 1:1, never shrunk
to fit a column, which is what turns form labels to mush. The spare pixels serve retina.
Two consequences for anyone writing a capture script:

- **Crop to the region the step is about.** `shot_region()` clips to the union of
  several selectors' bounding boxes; the artwork form's `Required` + `Pricing` fieldsets
  are 1043 CSS px tall, where the whole form is over 2000 and includes nothing the step
  mentions.
- **Keep the viewport narrow** (default 1100 px) so 1:1 fits a reading column.

**WebP, not PNG.** Same pixels, roughly a tenth the bytes: the first PNG run was 14 MB
for eight shots, the WebP one is 872 KB (~110 KB each, which is the budget this document
originally set). PNG is still *read*, so a hand-edited image can be dropped in.

## Writing a capture script for another guide

Add an entry to `CAPTURE_SCRIPTS` in
`gallery/management/commands/capture_howto.py`. Each has a `prepare` (database lookups —
these must happen before the browser starts, because Playwright's sync API makes the
script an async context and Django refuses ORM access from one; use `_db()` for the rare
fact that only exists mid-run), a `run`, `reset`/`cleanup`, and a `prose_only` set.

**Reuse the shared flow fragments.** `_open_signup`, `_fill_signup`,
`_open_email_confirmation`, `_fill_login`, `_log_in`, `_fill_profile` and `_fill_artwork`
perform the actions and return the form's selector, leaving each script to decide what to
photograph and how to crop it — the framing is what differs between guides, not the
driving. Several guides describe the same screens from different angles, and that is fine:
someone adding an artwork should not have to read the submission guide.

**Don't walk signup unless the guide is about signup.** `_create_verified_artist()` makes a
ready-to-use account in `prepare`, which skips the reCAPTCHA workaround and the
email-confirmation round trip entirely, and keeps the script from mutating seeded accounts
that other guides depend on. Only `how-to-sign-up-for-an-account` and `submit-artwork`
need `needs_recaptcha_off`.

**Crop for the reader, not for the element.** A step that says "find this control" needs
the control *in context*: cropping to the element itself produced a 22×19 px picture of the
word "Edit" and a 32×18 px "New" — technically correct, useless. Crop to the card or
section that contains it. Conversely, a whole long form is worse than the one fieldset the
step names. **Look at the output** — every framing problem so far has been invisible in
the step list and obvious in the image.

**`shot_region` resolves selectors with Playwright, not `document.querySelector`**, so its
text engine is available: `.section-label:has-text("Artworks")` picks the right one of
several identical sections. Plain CSS can only take the first match, which silently
cropped the wrong half of the Me page.

**`.first` is a trap when a page has several of the same control.** The reviews dashboard
carries a `.cs-launch-btn` per juror *before* the one beside the Artworks heading, and the
per-juror ones pass `?juror=<id>`, so `locator('.cs-launch-btn').first` opened the
slideshow restricted to one juror — producing a "REVIEWS (1)" panel for a step about
seeing *every* juror's scores. Nothing failed; the picture was just quietly wrong. Match
the label the guide names (`get_by_role('button', name='Curation Slideshow')`).

**A capture that performs a real action must undo it.** The review-slideshow script scores
an artwork, which is the step it illustrates — and left the review behind on the seeded
show, so runs accumulated and the jury data every other guide is captured against drifted.
The jury scripts snapshot the existing reviews in `prepare` and delete anything new in
`cleanup`. Check the DB is unchanged after `--all` when a script writes to shared fixtures
rather than to its own throwaway account.

**Slideshow overlays are built in JS with element *ids*, not classes** (`#rs-criteria`,
`#cs-scores`, `#cs-thumbs`). The class names that look equivalent are on the repeated rows
inside them.

**Locate controls by the words the guide uses.** `rec.control('Sign Up')` matches a link
*or* a button with that text, because the reader cannot tell the difference and the guide
does not say. That is what makes a rewording fail the run. Do not add `data-testid`
hooks: this is run on demand, and text matching is the mechanism, not an obstacle to it.

**`prose_only` is a declaration, not a skip.** Any step neither captured nor listed there
is reported as uncovered, so adding a step to a guide surfaces here instead of quietly
going un-illustrated.

## What the first capture found

Both of these are real reader-facing bugs that the written guides had papered over. The
prose is fixed; the code issue is not.

1. **The guides omitted email confirmation entirely.** `ACCOUNT_EMAIL_VERIFICATION =
   'mandatory'` in all environments, so a new artist signs up and is *not* signed in:
   they land on "Verify Your Email Address", must open an emailed link, click **Confirm**,
   and then **log in** — three actions before reaching the profile page. The public
   submit guide said "after signing up you land on your artist profile edit page", and
   the sign-up guide skipped it too. This is very likely a large part of why new artists
   were getting lost. Both guides now describe it (the public one grew from 7 steps to 9).

2. **Pricing is required, and both guides said otherwise.** `_require_explicit_pricing()`
   deliberately blanks the model's `on_request` default so an artist has to make a
   conscious choice — a sound decision, but "How to add artworks" called pricing
   *optional* and the submit guide never mentioned it. A reader filling in exactly what
   the guide listed hit a native browser tooltip and could not save. Both now say it is
   required.

## Known code issue, not fixed

`static/js/artwork_form_validate.js` → `updatePriceField()` treats the empty
`pricing_type` as its `else` branch, i.e. the same as "For Sale", and sets
`price.required = true`. So on a fresh New Artwork form the browser reports **price** as
the problem when the real problem is that pricing has not been chosen yet — and
server-side `clean()` only requires a price for "For Sale", so the JS is stricter than
the form. Suggested fix: treat `''` like the non-priced options (hide the row, clear
`required`) and let the unselected `pricing_type` be the only complaint.

Separately, `ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION` is off, which is what forces finding
#1's extra log-in step. Turning it on would shorten new-artist onboarding by a step;
that is a product decision, not a docs one.

## What already exists (do not rebuild)

- **`eatart/role_docs.py` → `HOW_TO_GUIDES`** — each guide has `title`, `roles`,
  `steps` (an ordered list of prose strings), optional `public_only`, optional
  `anchor`, optional `image_key`. The steps *are* the capture script.
- **`scripts/create_test_database.sh`** — seeds sites, shows, artists, jury data.
  Show dates are derived from the day it runs, so a capture in March and one in
  November produce the same pages. Seeds accounts at each step of the submission flow
  (`ready@`, `nophoto@`, `newcomer@`, `invited@`, `uninvited@`, all password `b8`).
  **It wipes `db.sqlite3`** — back it up first.
- **`manage.py make_test_artist --state {no-account,new-signup,no-photo,complete}`** —
  puts a single artist at an exact point in the flow. Local dev only.
- **`templates/public/howto.html`** — renders each guide as an `<article>` with its
  anchor as the `id`, each step's screenshot beneath its prose, and a "Walk me through
  it" button. That button needs `guide.steps` to stay a plain list of strings for its
  `|||`-joined `data-steps`, which is why `steps_with_images` returns a parallel
  structure instead of rewriting `steps`.

## Constraints

- **Local only, never production.** Curator views show artist emails and phone
  numbers. The command refuses to run outside `settings.LOCAL_DEV`.
- **Seeded state is required** for reproducibility.
- **Publishing needs AWS write credentials**; capturing does not. Only `--publish`
  touches the bucket.
- **Not every step screenshots well.** Four guides were checked and are poor candidates
  for stills: the room layout editor (21 steps of canvas drag-and-drop), the 3D view
  (`WebGLRenderer`, unreliable headless), the sites map (pulls live OpenStreetMap tiles
  from a CDN in `site_list.html`, so shots would be network-dependent and
  non-deterministic), and the pinboard's drag-to-reorder. Those want a short video or
  should stay prose. Declare them in `prose_only`.
- **Placeholder imagery is generated, not taken from `test_fixtures/`.** Those are real
  works by named artists, and a capture would show them uploaded under the script's
  fake artist name. Misattribution in a public help page is not worth the realism.

## Next

- **The signed-in submit guide** (`image_key` would default to `submit-artwork`) — starts
  from an existing account, so its script is the short one: sign in as `ready@example.com`
  and go. Doing this second guide is also the real test of whether the `Recorder` helpers
  generalise.
- **The other ~28 guides**, in whatever order is most useful. Curator and staff flows are
  the long ones.

## Open decisions

- **Should this gate releases?** Still open, and still the decision that matters. Text
  matching and on-demand runs were chosen deliberately (2026-07-28); putting this in CI
  would want `data-testid` attributes instead, which is real work across the submit-flow
  templates. On-demand has worked fine so far.
- ~~**How much coverage?**~~ Settled: S3, so bytes no longer constrain how many guides
  get pictures. What is left is whether every guide *deserves* them.
