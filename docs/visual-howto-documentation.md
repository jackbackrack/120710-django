# Visual how-to documentation

**Status: not started.** This is the agreed plan, written 2026-07-28 so it can be
picked up cold in a later session.

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

## What already exists (do not rebuild)

- **`eatart/role_docs.py` → `HOW_TO_GUIDES`** — each guide has `title`, `roles`,
  `steps` (an ordered list of prose strings), optional `public_only`, optional
  `anchor`. The steps *are* the capture script.
- **`scripts/create_test_database.sh`** — seeds sites, shows, artists, jury data.
  Show dates are derived from the day it runs, so a capture in March and one in
  November produce the same pages. Seeds accounts at each step of the submission flow
  (`ready@`, `nophoto@`, `newcomer@`, `invited@`, `uninvited@`, all password `b8`).
  **It wipes `db.sqlite3`** — back it up first.
- **`manage.py make_test_artist --state {no-account,new-signup,no-photo,complete}`** —
  puts a single artist at an exact point in the flow. Local dev only.
- **Stable guide anchors** — `guide['anchor']` overrides `slugify(title)`. The submit
  guide already uses `'submit-artwork'`, shared by its public and signed-in versions.
- **`templates/public/howto.html`** — renders each guide as an `<article>` with that
  anchor as its `id`, and a "Walk me through it" button that steps through `steps`.

## Two halves

### 1. Rendering (no browser needed — can be built any time)

- Optional per-step image in `HOW_TO_GUIDES`. Steps are currently plain strings, so
  either allow a dict per step or add a parallel `images` list — prefer whichever
  keeps `steps` readable, since it is also rendered as prose and used by the
  walkthrough widget's `data-steps`.
- `templates/public/howto.html` renders the image beneath its step.
- Convention: `static/img/howto/<anchor>/NN.png`, keyed off the stable anchor so a
  retitle doesn't orphan the images.

### 2. Capture (needs a browser)

Two routes, not exclusive:

**A — AI-driven (Claude in Chrome).** Read a guide's steps, drive the UI, screenshot
after each. Best for *authoring*: judgement is needed to turn prose into actions, and
an AI adapts when a button is renamed rather than breaking. Requires the Claude in
Chrome extension connected — as of 2026-07-28 it was installed but not detected;
`/chrome` completes the connection and a **new** session (not `--resume`) picks it up.

**B — Playwright management command.** `pip install playwright` +
`playwright install chromium`, then `manage.py capture_howto <anchor>`: seed, drive
headless, write the PNGs. Best for *repeating*: runs in CI, no AI in the loop. Brittle
where A adapts — a renamed button fails the run, which is the staleness signal.

**Recommended sequencing:** author the first capture with A (learn what each screen
should show), then write B to reproduce it automatically from then on.

## Constraints

- **Local only, never production.** Curator views show artist emails and phone
  numbers. Capture against a seeded local database.
- **Seeded state is required** for reproducibility, regardless of capture method.
- **Not every step screenshots well** — dragging in the layout editor, the 3D
  walkthrough, hover states. Those may want a short video, or stay prose.
- **Image churn.** ~60 screenshots at ~150 KB is fine in git; S3 alongside other media
  if it grows.

## Start here

The **submission-flow guide** (`anchor: submit-artwork`). It was rebuilt end to end on
2026-07-27, so its screens are well understood, and it is the flow real users were
getting lost in. Five or six shots, wired into the help page. That is enough to judge
whether the result justifies doing the other ~29 guides.

## Open decisions

- **Should this gate releases?** If yes it belongs in CI, and the capture scripts want
  stable `data-testid` attributes rather than matching on button text — text gets
  reworded (`"Sign up to submit"` became `"Submit"` on 2026-07-27). If it's run on
  demand, text matching is fine and that work is unnecessary.
- **Who is the audience?** A few polished walkthroughs for artists, or broad coverage
  for Jack, regenerated per release. This decides one-off versus CI job.
