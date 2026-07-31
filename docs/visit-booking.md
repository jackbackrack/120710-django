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

### Drop-in hours are pushed, arranged ones are offered

Slots carry which kind of block they came from all the way to the page, because they are not the
same offer: one is "the gallery is open, come in", the other is "somebody will make a point of
being here". Public hours cost nobody a special trip, so the page names them before the grid —
*"open to everyone Sun 1:00–4:00 PM … you are welcome to just turn up"* — and lists them first, in
green, above a quieter *by arrangement* group.

Encouraged is not required: an arranged slot can still be booked, and there is a test saying so.

### What is on that day

Each day carries the show that is up, linked, or says **Between shows — for anything other than
seeing a show**. Somebody choosing
between two afternoons is usually choosing between two shows, and days with nothing hanging are
still offered rather than hidden — a visitor may well want to come and see the space.

Only `published` shows are named. A draft is not something to announce, and an open call is not yet
on the walls.

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

### A subscribable feed, as well

`/visits/<token>.ics` is every booked visit as a calendar. In Google Calendar: *Other calendars →
+ → From URL*. It appears as its own calendar you can colour or switch off, and cancelled visits
simply vanish from it on the next poll — no `SEQUENCE` dance, because nothing is being updated.

**It complements the invitations, it does not replace them.** Google refreshes a subscribed
external calendar on its own schedule — commonly hours — and ignores any refresh hint in the file.
So the invitation is what tells you about a booking made this morning; the feed is the standing
overview.

**The URL is the credential**, and it carries visitors' names and email addresses. A subscribed
calendar cannot sign in — Google fetches it with no cookies of ours — so there is nowhere else to
put the secret. Hence a random per-site token rather than a slug, `private` caching, a
`noindex` header, a 404 (never a 403) for a wrong token, and a **new address** button, since
changing the URL is the only way to deal with one that has got out. It is shown on the staff
Visits page with that warning next to it, and is never linked from a public page.

The shows feed at `/shows.ics` is public because shows are; this one is the opposite, and the two
should not be confused.

## What this deliberately does not do

**It does not read your calendar to avoid clashes.** That is the expensive half — either a secret
iCal feed to fetch, cache and parse (with recurrence rules, which is not worth hand-rolling) or
OAuth with a sensitive scope and a verification step.

It buys little here, because slots are shared and each booking arrives as an invitation you can
simply **decline** if the time turns out to be bad. For a known-busy stretch, add a closure.

### Closures

As many ranges as you like, each inclusive of its last day, and they may overlap — a week away
inside a month between shows is ordinary. Where two overlap, the stricter one wins, or a partial
closure would quietly reopen a venue a full one had shut.

`appointments_only` is for the case date ranges alone express badly: **you are away, so no
appointments, but somebody is covering the public Sunday hours.** Without it that is one Mon–Sat
row per week of an absence, and getting one wrong closes a Sunday that was staffed. With it, one
row over the whole absence removes the arrangeable hours and leaves the drop-in ones.

Worth revisiting only if declining becomes the annoying part. If it does, the cheaper of the two
routes is Google's per-calendar **"Secret address in iCal format"** fetched server-side — no OAuth
at all — and staleness is tolerable precisely because a missed busy block costs one declined visit
rather than a collision.

## Timezones

`TIME_ZONE` is `UTC` and `USE_TZ` is on, so **Django converts every aware datetime to UTC when a
template renders it** unless something says otherwise. Slots are built correctly in the venue's
zone and were then displayed in UTC — a noon opening in Berkeley appeared as a 7pm slot. The
datetimes were never wrong; only the rendering was.

Two fixes, because there are two situations:

- **Single-venue pages and emails** — the booking page, the confirmation, the cancel page and both
  messages render inside `timezone.override(site_timezone(site))`. Every `{{ }}` in them is then
  in the venue's zone without per-template ceremony.
- **The staff visits list** — it spans venues, so no single zone is right. Each row is converted
  before it reaches the template and the page uses `{% localtime off %}` so Django leaves the
  already-correct values alone.

The `.ics` is a separate matter and was always correct: `DTSTART`/`DTEND` are absolute UTC
instants, which is what a calendar wants. A test pins that too, so fixing the display cannot
quietly break the invitation.

Anything added here that shows a time needs one of those two treatments. The booking page also
names the zone it is showing, so a visitor in another one is not guessing.

## Settings, per venue

| Field | Default | |
| --- | --- | --- |
| `visits_enabled` | off | needs structured opening hours first, or it offers nothing |
| `visit_slot_minutes` | 30 | |
| `visit_capacity` | 0 | people per slot; 0 is no limit |
| `visit_lead_hours` | 2 | slots sooner than this are not offered |
| `visit_horizon_days` | 30 | how far ahead booking opens |
| `arrival_note` | — | what to do at the door, e.g. "Ring the bell" |

`arrival_note` goes in the confirmation email, on the page after booking, and into the calendar
invitation the gallery gets — the confirmation being the message somebody has open on their phone
while standing outside. It is separate from *Getting here*, which is about the journey.

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

**Booking is the only way to arrange a visit**, and the wording is kept that way on purpose: the
empty state does not offer an address to write to, the confirmation gives the phone number framed
for the day itself rather than as a way to book, and the "come visit" block in every campaign links
to the booking page instead of saying "to arrange a time, phone or email". A visit arranged by
email is not in the calendar, and then the calendar stops being the record of who is coming.

This goes further than the booking flow: **no public page prints the gallery's email address or
phone number at all.** The Visit page's "by calling … or emailing …", the Contact page's `email:`
and `phone:` lines, the contact line in every campaign footer and the phone number in the visit
confirmation have all been removed. `Site.email` and `Site.phone` still exist and are still used —
the address receives visit notifications and is the `ORGANIZER` on calendar invitations — they are
simply not published.

**One exception, deliberately: the privacy page.** A policy that offers no way to make a data
request is not a policy, and "write to the gallery" with no address to write to is worse than
nothing. If you want the address off that page too, replace it with a contact form rather than
deleting it. There is a note to that effect in the template.

A **signed-in** visitor is not asked their name or address — they have told us both already, so
the fields are dropped rather than pre-filled, and the values are read from the account on the
server. Pre-filling would let a hidden field book a visit in somebody else's name.

There is no confirm-your-email step before a booking counts. A booking is not a mailing-list
signup: since slots are shared, a spammed booking wastes nobody's place, and making every genuine
visitor click a second link would cost more than it saves. The honeypot and reCAPTCHA that protect
the other public forms protect this one.
