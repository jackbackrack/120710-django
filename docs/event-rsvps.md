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
| Reply form and count | the event page, `gallery/templates/gallery/_rsvp_form.html` |
| "Let us know you are coming" | the opening mailing, above add-to-calendar |
| Change your answer | `/rsvp/<token>/`, linked from every email |
| Who replied | Mailing List → Visits, staff only |
| The reminder | `manage.py send_event_reminders`, daily |

One reply per address per event, enforced by a constraint: a second reply is somebody changing
their mind, not a second guest, and two rows would inflate what the gallery caters for.

Signed in, name and email are not asked for and are read from the account server-side — a
pre-filled field can be edited, which would let somebody reply in another person's name.

## What was deliberately left out

- **A live count from zero.** See the threshold above.
- **Public guest names.** See the audience above.
- **Capacity or ticketing.** Both turn a warm invitation into an administrative transaction, and
  nothing here has a room limit worth enforcing.
- **An account requirement.** Same reasoning as visit booking: it would cost more replies than the
  spam it prevents. The honeypot and reCAPTCHA that protect the other public forms protect this.
