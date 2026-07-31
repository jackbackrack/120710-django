# Replying to an event

People reply **yes, maybe or no** to an opening, and get a reminder the day before.

## Why the reminder is the feature

One announcement three weeks out is a single shot at a date nobody has planned around yet. What
goes wrong between that email and the night is almost always **forgetting** — not deciding against
it.

A reminder to the whole mailing list is a second campaign, and slightly spammy. A reminder to
somebody who said they were coming is a service they asked for. **The RSVP is what earns the right
to send it.** The headcount is a side effect, not the point.

    manage.py send_event_reminders --dry-run
    manage.py send_event_reminders

Meant for a daily scheduled run. Safe to run more than once: each reminder is marked as it goes,
so a cron that fires twice does not mail anybody again. It is marked **after** sending, never
before — a failed send is retried on the next run, because a reminder that never arrives is the
whole failure this exists to prevent.

## Three answers, not one button

- **Maybe** is the honest reply weeks out. Offering it is what stops somebody closing the page
  having said nothing — and a maybe is the person a reminder helps most, because they have not
  decided and the night before is when they will. They get reminded.
- **No** looks useless and is not. It takes them off the reminder, which is the difference between
  a service and a nuisance, and it tells the gallery interest existed even where attendance did
  not.

There is no separate cancellation. Changing your answer to *no* **is** the cancellation — one
mechanism rather than two, and it reads better to the person doing it. Changing an answer clears
`reminded_at`, so somebody who switches from no to yes the week before is still reminded.

## The count

`Event.rsvp_count` is **heads, not replies** — it sums party sizes, because that is what the
gallery caters for.

It is shown publicly only once it reaches `Event.RSVP_COUNT_THRESHOLD` (8). Below that a count
discourages: "3 coming" reads as an empty room, and early in a cycle it is always 3.

**Names are never shown.** Partiful's audience is friends; a gallery's includes collectors and
press who will not reply at all if the list is public. The count is anonymous, and staff see the
detail behind a login on **Mailing List → Visits**.

## "+ how many", not "will you attend"

Party size defaults to 1 and is asked for on the same line as the answer, so bringing somebody is
the default rather than a decision. Arriving alone is one of the real reasons people do not come.

A *no* is forced back to a party of 1 — a decline with four guests is a contradiction, and the
number would otherwise sit in the arithmetic waiting for somebody to widen what counts.

## Where it appears

| | |
| --- | --- |
| RSVP button and calendar glyph | on the same line as the date, on the **show page** and on every **show card** |
| A page that is only the reply | `/event/<pk>/rsvp/` |
| Reply form and count | the event page, `gallery/templates/gallery/_rsvp_form.html` |
| "Let us know you are coming" | the opening mailing, above add-to-calendar |
| Change your answer | `/rsvp/<token>/`, linked from every email |
| Who replied | Mailing List → Visits, staff only |
| The reminder | `manage.py send_event_reminders`, daily |

One reply per address per event, enforced by a constraint: a second reply is somebody changing
their mind, not a second guest, and two rows would inflate what the gallery caters for.

Signed in, name and email are not asked for and are read from the account server-side — a
pre-filled field can be edited, which would let somebody reply in another person's name.

## Reaching people who never open the event page

Most people meet an event as a line on a **show page** — or, before that, as one line on a **show
card** — and will not click through to its own page to find a reply button that only exists
there. So each future event carries a small **RSVP** button and a calendar glyph on the same line
as its date, in both places.

Both come from one partial, `_event_actions.html`, because the whole reason they exist is that
people do not click through; they have to look and behave the same wherever an event is listed.

**And there is one show card, `_show_card.html`, used by every listing including the home page's
featured one** — `big=True all_events=True banner="Current Show"`. It was a hand-written copy
until it was found to be a release behind: still printing the year, still offering no way to
reply, and still carrying an `{% if show.location %}` line for a field the model does not have.
The featured card is the only one that lists *every* event rather than just the next, so it is
also the only place a dead RSVP could appear beside a past one; `_event_actions.html` guards
itself, and a test pins it.
They are deliberately sized down in CSS: they sit inside `h3` and `h4`, and would otherwise
inherit the heading size and dwarf the date they belong to.

A card only ever shows `get_next_event`, which is future-only by construction — but there is a
test pinning that, since a change there would silently put a dead RSVP button on every card.

The button goes to `/event/<pk>/rsvp/` — a page with nothing on it but the three answers, a
headcount and Send. That duplicates the form embedded on the event page, deliberately: somebody
already reading about an event should not have to go anywhere, and somebody glancing at a show
page needs a link that obviously means *reply* rather than one that lands them on a full page
where the intent gets lost.

Both are the same three buttons from the same `rsvp_choices` tag, so they cannot drift.

**Add-to-calendar has three sizes**, each earning its place:

| | |
| --- | --- |
| full | an event's own page — names both calendars, since which one is the actual choice |
| `compact` | the agenda, where it repeats on every future row |
| `icon_only` | beside a heading on a show page, where there is room for a glyph and nothing else |

A glyph alone is a guess for the reader, so `icon_only` leans on the title and the accessible
name. That is a real cost and the reason it is not the default.

There is deliberately **no "which calendar?" page**. The two links *are* that choice, and a page
between them would be a click that adds nothing.

**Nothing is offered for an event that has already happened** — no RSVP, no calendar links, on any
surface including a mailing, which can be read long after it was sent. The partial guards itself
so a surface added later cannot forget, and each call site guards too so no empty wrapper is left
behind. An event *today* is not past: the closing mailing goes out on the morning of the closing.

## What was deliberately left out

- **A live count from zero.** See the threshold above.
- **Public guest names.** See the audience above.
- **Capacity or ticketing.** Both turn a warm invitation into an administrative transaction, and
  nothing here has a room limit worth enforcing.
- **An account requirement.** Same reasoning as visit booking: it would cost more replies than the
  spam it prevents. The honeypot and reCAPTCHA that protect the other public forms protect this.
