# The mailing list

We own the list. Resend is a delivery service we hand finished messages to, one at a time.
That split is the whole design, and it is worth being precise about, because it determines
what breaks when something breaks.

## What we do, and what Resend does

**120710.art does everything about *who* and *what*:**

| Job | Where |
| --- | --- |
| Holds the list — one `Subscriber` per person, one `Subscription` per person-per-list | `gallery/models/subscribers.py` |
| Decides who a campaign goes to (`is_subscribed`, right list) | `gallery/campaigns.py::recipients` |
| Skips anyone already sent it | `gallery/campaigns.py::pending` |
| Renders the email — MJML through Django's template engine, per recipient | `gallery/campaigns.py::render_campaign` |
| Mints the signed unsubscribe token and the RFC 8058 one-click headers | `gallery/campaigns.py::unsubscribe_token` |
| Serves the unsubscribe page and the one-click POST | `eatart/views/unsubscribe.py` |
| Records who was actually sent each campaign | `CampaignDelivery` |
| Suppresses on bounce and complaint | `gallery/webhooks.py` |
| Refuses to send an untested draft | `Campaign.can_send` |

**Resend does the delivery, and nothing about the list:**

- Accepts each message through its API (via `django-anymail`'s Resend backend).
- Signs it as our sending domain — DKIM and SPF — which is what stops Gmail filing it as
  forgery.
- Talks SMTP to the recipient's mail server, retries a temporary refusal, gives up on a
  permanent one.
- Reports back what happened, as a webhook: delivered, bounced, complained.
- Keeps its own account-level suppression list, independently of ours.

Resend does **not** know our list. There is no Resend audience, no list stored on their
side, no unsubscribe link of theirs in our mail. Every message we send is, from Resend's
point of view, a one-off transactional message that happens to be one of nine hundred.

The two consequences worth knowing:

**The unsubscribe link is ours.** It is a signed token naming one subscription, with no
expiry, resolved by our own view. An unsubscribe in a two-year-old email still works. If we
had used a Resend audience, that link would be theirs, and leaving the provider would break
every one of them.

**Campaigns go through Resend; account mail does not.** Transactional mail — email
confirmations, password resets, acceptance letters — stays on smtp2go, on a separate
`EMAIL_BACKEND`. This is deliberate, and not a historical accident: on a shared provider, one
reader marking a newsletter as spam suppresses that address account-wide, and the next thing
that address silently fails to receive is their acceptance letter for a show. The split means
a mailing-list problem can never take down account mail.

## Sending

Compose, preview, test and send are one page: **Campaigns** in the nav (staff only).

The send guard is the important part. `edited_at` moves whenever the content changes;
`test_sent_at` moves when a test goes out; sending is refused unless the test is the later of
the two. So "sent the draft with the placeholder still in it" is structurally impossible
rather than a matter of remembering. Change the subject after testing and the test stops
counting.

A send does **not** run in the web request. It runs in a background thread, and the page
returns immediately with a progress bar that refreshes every fifteen seconds. Doing it in the
request would hold a worker for the duration and hand the operator a gateway timeout, while the
send carried on invisibly behind the error page — so the one person who needed to know how it
went was the one person who could not find out.

### One request per message, and the rate limit

There is no batch API call. `django-anymail` makes **one HTTP request to Resend per message**,
because every recipient's copy differs — the unsubscribe link is per-person. So a
1,200-person list is 1,200 API requests.

That matters because Resend's default allowance is about **two requests a second**, and
exceeding it returns 429. Unthrottled, a list of any size trips that within the first few
seconds, which would look exactly like the mailing list being broken on the one day it
matters. So the send paces itself:

    CAMPAIGN_MESSAGES_PER_SECOND=2      # raise only after confirming a higher account limit
    CAMPAIGN_SEND_IN_BACKGROUND=false   # send inline instead; what the tests do

At two a second, 1,200 messages takes about ten minutes. That is why it is a background
thread and not a request.

### Two kinds of failure, handled differently

The distinction the code turns on is **whose fault it is**, because the right response to each
is the opposite of the other.

**The provider is having a bad minute** — 429, 5xx, network drop. Retried per message, three
attempts with doubling backoff, because a rate limit is ordinary weather and should cost a
pause rather than a stopped send. If it still fails, those people are left with **no delivery
record at all**, so they are still owed the mailing: the campaign is left `failed` and Resume
tries them again. Writing them off for an outage they had nothing to do with would mean they
never hear from us again.

**The address is bad** — a 4xx naming the recipient. Not retried; a malformed mailbox is not
going to start working. Instead it is treated as exactly what it is: a **hard bounce, told to
us up front instead of by webhook ten minutes later.** So:

- The rejection is recorded on the delivery row, with the provider's own words.
- The person is unsubscribed from every list, reason `bounced` — the same thing the webhook
  does.
- **The campaign still completes as `sent`.** There is nothing a resume could usefully do about
  an address that will never accept mail, and leaving the campaign `failed` would mean pressing
  Resume forever against it.
- The campaign page names the addresses and what the provider said about each. Nothing needs
  doing about them; it is a report, not a task.

This is the common case on any real list — a handful of dead addresses out of a thousand — so it
is deliberately a non-event that needs no cleaning up by hand.

**One backstop over both:** ten consecutive failures of either kind stops the send. No point
working through 1,200 addresses against a provider that is refusing everything. It stops, and it
is resumable.

Because the send has no request to build URLs from, links in campaign mail come from
`SITE_BASE_URL`. Getting that wrong breaks the unsubscribe link in mail that has already
gone out, so it is an explicit setting rather than a fallback buried in the mail code.

## Writing the body

Two authoring paths, and they are not alternatives — they are for different jobs.

**Markdown, for a one-off.** Typed into the campaign form, changed in the browser, no deploy.
The supported vocabulary is deliberately small, because every construct has to survive Outlook:

| Syntax | Result |
| --- | --- |
| `# ` `## ` `### ` | headings |
| blank-line-separated text | paragraphs |
| `- ` `* ` `+ ` items | bullet list (one level, no nesting) |
| `1. ` items | numbered list |
| `**bold**` `*italic*` | emphasis — cannot span a line break |
| `[text](url)` | link |
| `![alt](url)` | image; anything after it becomes a caption |
| `[[Label\|url]]` | a button |

Anything else — blockquotes, tables, code, rules, bare URLs — **renders as the literal text
typed**, rather than raising. That is a deliberate trade rather than an oversight: the send guard
means somebody always looks at a real copy first, so a visible oddity gets caught, whereas a hard
error on a stray character would block a mailing over nothing. The cost is that the failure is
silent, so the test send is doing real work — read it, do not just check it arrived.

A list is all-or-nothing: every non-empty line of a block must be an item, otherwise the block
is a paragraph. A paragraph that happens to contain a line starting with a dash is far more
likely to be prose than a list somebody typed wrong.

**An MJML template, for a recurring shape.** A file under `templates/email/campaigns/`, rendered
through Django's template engine — so it reaches the ORM and nobody retypes a date. Full layout
vocabulary: sections, columns, spacers, per-client overrides. The cost is that it lives in the
repo, so changing it is a deploy.

**Both at once.** A template can place the author's prose inside its own layout, which is how a
recurring format avoids meaning identical wording every time:

    {{ campaign_body }}          a whole <mj-section>, to drop between sections
    {{ campaign_body_blocks }}   just the contents, to drop inside an existing <mj-column>

Use the wrong one and you nest an `mj-section` inside an `mj-column`, which MJML rejects with an
error mentioning neither. `show_announcement.mjml` does this for an optional curator's note.

Do not grow the Markdown subset much past this. It is regex-based, which is fine for a closed
vocabulary and rots quickly for an open one; and swapping in a general Markdown library would put
arbitrary HTML into email, which is how the Outlook problems MJML exists to solve come back. Past
lists, a template is the right answer.

## When a send stops part-way

It will happen: the provider rate-limits, or the site gets deployed mid-send. This is the
failure that would actually hurt on a list of any size, so it is designed for rather than
hoped against.

Every person sent a campaign gets a `CampaignDelivery` row, written **immediately after that
one message is accepted**. Per message rather than per batch costs nothing, since the backend
makes one API call each anyway — so a resume never repeats a message.

The ordering is deliberate: a record written *before* the send would, on a crash between the
two, convince a later resume that somebody had been mailed when they had not, and silently drop
them from the list. Written after, the same crash costs one duplicate at most. Duplicates are
the tolerable failure here; omissions are not.

So a stopped send is recoverable:

- The campaign page says **"This send stopped before it finished"**, with a count of how many
  of how many people received it, read from the delivery records rather than guessed.
- **Resume** mails only subscribers with no delivery record. Pressing it when the send had in
  fact finished mails nobody. It is safe to press without knowing how far the last attempt
  got, and it cannot loop: a refused address has a delivery row too, so it is never retried.
- The recipient list is re-read on resume, not frozen from when the send began, so somebody
  who unsubscribed in between does not get it.

Two ways a send stops, and both are covered:

- **It raised.** Status becomes `failed`.
- **The process vanished** — a deploy, an OOM kill, Railway moving the container. Nothing can
  report that from the inside, so it is inferred from absence of progress: status `sending`
  with a `progress_at` older than `Campaign.STALL_AFTER` (ten minutes) is treated as stopped.
  This is the more dangerous of the two, because it looks like work in progress rather than a
  problem.

`progress_at` moves on elapsed time — every `PROGRESS_EVERY_SECONDS` (30) — not per message.
Comfortably inside `STALL_AFTER`, so a slowly-throttled send is never mistaken for an abandoned
one and invited to be resumed alongside itself.

Concurrent sends cannot both start. Claiming a campaign is a compare-and-set on the status
column, so a double-clicked button, two workers behind one URL, or Resume pressed on a send
that is genuinely still running all lose the race and send nothing. The unique constraint on
`CampaignDelivery` would catch the duplicate too, but only after the mail had gone out.

### Warming up a new sending domain

Pacing inside one send does nothing for a domain with no sending history. A thousand messages on
day one is filtered on volume however gently they are spaced, so the ramp has to be across days,
which means a send that deliberately stops short:

    ./env/bin/python manage.py send_campaign 12 --limit 100           # day one
    ./env/bin/python manage.py send_campaign 12 --resume --limit 300  # day two, and so on

A limited pass leaves the campaign **`paused`** rather than `failed` — stopping on purpose is not
the same as breaking, and the page says so in those words. Everything about resume applies: each
pass only mails people with no delivery record, so no ordering or bookkeeping is needed.

The second pass needs `--resume` as well as `--limit`. Being told "there is nothing to resume" is
better than a stray `--limit` starting a finished campaign over.

### From the command line

Same engine, same guards, no web process involved. Use it for a send interrupted by a deploy,
or a list large enough that minutes of sending inside a web container is the wrong place for
it:

    ./env/bin/python manage.py send_campaign 12 --dry-run    # who would get it
    ./env/bin/python manage.py send_campaign 12              # send
    ./env/bin/python manage.py send_campaign 12 --resume     # finish a stopped send

## Joining a list: single opt-in plus a welcome email

Both signup forms are **single opt-in** — submitting subscribes you, with no link to click first.
That was chosen over double opt-in deliberately, and the reasoning matters if anybody revisits it:

- Double opt-in loses a real share of signups at the confirmation step.
- What it protects against is small here. The public form takes a handful of submissions a week;
  the actual deliverability exposure is the imported list of roughly a thousand addresses of
  unknown age, and confirming new signups does nothing about that.

What replaces it is a **welcome email sent immediately**, which buys most of the same protection
with none of the friction:

- A dead address bounces on that one message, and the bounce webhook removes it **before** it ever
  receives a campaign. A typo costs one bounce instead of bouncing on every mailing for years.
- Anybody signed up by somebody else gets a prominent one-click unsubscribe straight away, rather
  than finding out months later.
- It is an engaged send, which is the useful kind of volume on a young sending domain.

It carries the same RFC 8058 one-click headers as a campaign, since it is the message most likely
to reach somebody who never asked to be on the list.

It is **transactional**, so it goes through the normal backend rather than Resend, and a failure to
send it is logged and otherwise ignored — the person is already subscribed by then, and refusing a
subscription because our own mail server was briefly unhappy is the worse outcome.

The artist profile form has a subscribe checkbox and deliberately sends no welcome email: that
person is signed in, and their address was already proved deliverable by the account verification.

## Subscribers

**Subscribers** in the nav (staff only). Per-person only: look somebody up, take them off one
list or all of them, add somebody who asked, delete somebody who wants to be erased. There is
no bulk action, deliberately — a button that could unsubscribe hundreds of people would be one
misclick from destroying a list.

Two asymmetries in that page are on purpose:

- **A bounce or a complaint cannot be undone from the UI.** "Add back" appears only for
  somebody who asked to be removed. Re-adding an address that reported you as spam is the
  single worst thing you can do to your deliverability.
- **Delete is not the same as unsubscribe, and is the riskier one.** The unsubscribed row is
  exactly what stops a future import of an old spreadsheet from mailing somebody again.
  Deleting throws that protection away along with the person, so use "remove from all" for an
  ordinary unsubscribe and keep delete for an actual erasure request.

Subscribers are not people on the site. They have no account, no artist profile, and no public
page.

### Importing

    ./env/bin/python manage.py import_subscribers export.csv --dry-run
    ./env/bin/python manage.py import_subscribers a.csv b.csv --site 120710

Several files at once, and the same address across them is folded into one person on two
rules: **any opt-out anywhere wins**, and **the first non-empty name is kept**. A Mailchimp
export contains unsubscribed and cleaned members alongside subscribed ones, and importing the
lot as subscribed would mail people who have already said no — which is both unlawful and the
fastest way to earn spam complaints on a new sending domain.

## Bounces and complaints

Resend posts delivery events to `/anymail/resend/tracking/`, signed with Svix; anymail
verifies the signature. A bounce or a complaint unsubscribes that person from **every** list,
with the reason recorded, without anybody having to watch a dashboard.

Everything else Resend reports is discarded. Our mail carries no tracking pixel and no
rewritten links, so we do not know whether anybody opened one or what they clicked — and the
privacy page says so, which is why the webhook must keep throwing that data away.

After a deploy, check the webhook is reachable:

    curl -s -o /dev/null -w '%{http_code}\n' https://www.120710.art/anymail/resend/tracking/

**405** means it is wired up (it only accepts POST). **404** means something is eating the
route and bounces are going nowhere.

## Testing it for real

Resend has simulator addresses that behave like the failure you want to see, without
involving a real recipient:

| Address | What happens |
| --- | --- |
| `delivered@resend.dev` | delivered normally |
| `bounced@resend.dev` | hard bounce → our webhook unsubscribes them everywhere |
| `complained@resend.dev` | spam complaint → same |
| `suppressed@resend.dev` | rejected by Resend's own suppression list |

A worthwhile rehearsal on a list nobody reads:

1. Add the four simulator addresses plus your own to a test list from the Subscribers page.
2. Compose a campaign to that list, preview it, send yourself a test.
3. Send it. Watch the progress bar finish.
4. Check the Subscribers page: `bounced@` and `complained@` should now be off every list, with
   the reason showing.
5. Click the unsubscribe link in your own copy. Confirm it takes you off that list only, and
   offers to take you off everything.
6. To rehearse a resume, send with `CAMPAIGN_SEND_IN_BACKGROUND=false` from a shell and
   interrupt it, or simply redeploy mid-send — then check the page offers Resume with the right
   count, and that resuming does not mail the ones already reached.

## Also see

- `docs/reset-art-cutover.md` — the DNS and domain side of the move.
- How-to guides, in-app: *How to send a mailing to the list*, *How to finish a mailing that
  stopped part-way*, *How to manage subscribers*.
