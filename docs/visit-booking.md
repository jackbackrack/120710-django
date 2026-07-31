# Booking a visit

Visitors pick a time on the website; the gallery finds out by email, as a calendar invitation that
Google Calendar adds by itself.

There is **no Google integration**. No OAuth, no scopes, no Cloud project, no API keys, no refresh
tokens, nothing to go stale, and nothing to break when Google changes a policy. That is not a
compromise — it is the reason this was worth building rather than embedding somebody else's
scheduler.

## The decision everything else follows from

**Slots are shared.** Several people may book the same half hour, because for a gallery that is
fewer appointments to keep rather than a clash.

That one choice removes almost everything that makes scheduling hard:

- no locking, and no double-booking race
- no slot held while somebody fills in a form, and so nothing to expire and clean up
- no reconciliation between what was offered and what was taken
- a booking is simply a row; availability is a pure function of the opening hours

Set `Site.visit_capacity` if you want a ceiling anyway — a school group of twelve is worth one.
Zero means no limit.

## How a slot is decided

Slots are **computed, never stored**. For each day in the horizon:

1. Take the venue's structured opening blocks for that weekday — `Site.open_periods_on(day)`,
   which has already removed any `SiteClosure`
2. Step through in `visit_slot_minutes` increments
3. Drop any slot that would **finish** after closing time — offering 3:45 for a half-hour visit at
   a gallery that shuts at four is how somebody arrives to a locked door
4. Drop anything sooner than `visit_lead_hours` from now
5. Drop anything already at capacity

Appointment-only hours **are** offered. That is the point of them: not open to the public, but a
time somebody can ask for.

The slot is re-checked on submission, not trusted from the form — the page may have been open for
an hour, and the notice period alone will have moved on.

## Telling the gallery

A message carrying a `text/calendar` **alternative part** with `METHOD:REQUEST` *is* a calendar
invitation. Google Calendar adds it to the owner's calendar without being asked. `METHOD:CANCEL`
with the same `UID` takes it away again.

Two things this depends on, and both are easy to get wrong:

- **The same `UID` for the invitation and its cancellation.** Different UIDs leave the original
  sitting in the calendar for good.
- **A higher `SEQUENCE` on the cancellation.** A calendar client ignores an update whose sequence
  has not advanced, so a cancellation sent at the same sequence is silently dropped.

It goes as an *alternative part*, not an attachment — an attachment is a file at the bottom of a
message, an alternative part is an invitation to act on.

The visitor's copy uses `METHOD:PUBLISH` instead: they made the booking, so their calendar should
just take it rather than asking them to RSVP to themselves.

One thing to check once, in Google Calendar settings: **"Add invitations to my calendar"** defaults
to *"From senders I know"*. Mail from the gallery's own address qualifies.

## What this deliberately does not do

**It does not read your calendar to avoid clashes.** That is the expensive half — either a secret
iCal feed to fetch, cache and parse (with recurrence rules, which is not worth hand-rolling) or
OAuth with a sensitive scope and a verification step.

It buys little here, because slots are shared and each booking arrives as an invitation you can
simply **decline** if the time turns out to be bad. For a known-busy stretch, add a `SiteClosure`.

Worth revisiting only if declining becomes the annoying part. If it does, the cheaper of the two
routes is Google's per-calendar **"Secret address in iCal format"** fetched server-side — no OAuth
at all — and staleness is tolerable precisely because a missed busy block costs one declined visit
rather than a collision.

## Settings, per venue

| Field | Default | |
| --- | --- | --- |
| `visits_enabled` | off | needs structured opening hours first, or it offers nothing |
| `visit_slot_minutes` | 30 | |
| `visit_capacity` | 0 | people per slot; 0 is no limit |
| `visit_lead_hours` | 2 | slots sooner than this are not offered |
| `visit_horizon_days` | 30 | how far ahead booking opens |

## Where things are

| | |
| --- | --- |
| Slots, invitations, emails | `gallery/visits.py` |
| The booking and cancel pages | `eatart/views/visits.py` |
| Staff list of who is coming | `gallery/views/visits.py` → Mailing List → Visits |
| Opening hours | `docs/mailing-list.md` has the formatting rules; the model is `OpeningHours` |

Cancellation links are signed tokens carrying the booking id *and* the email address, so a
recycled primary key cannot cancel somebody else's visit. Cancelling is a **POST** — mail clients
prefetch links, and a scanner must not be able to cancel a visit on the visitor's behalf.

There is no confirm-your-email step before a booking counts. A booking is not a mailing-list
signup: since slots are shared, a spammed booking wastes nobody's place, and making every genuine
visitor click a second link would cost more than it saves. The honeypot and reCAPTCHA that protect
the other public forms protect this one.
