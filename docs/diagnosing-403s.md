# Diagnosing a 403

A visitor reports "Forbidden" on a form they cannot save. This is how to find out which of
several unrelated faults it was, in one log line rather than several days.

## What produces a 403 here

| Cause | What they see | Logger |
| --- | --- | --- |
| CSRF rejection | "That didn't go through", with the site's nav | `eatart.views.csrf` and `django.security.csrf` |
| A permission check (`UserPassesTestMixin.test_func`) | bare white "403 Forbidden", no nav | `django.request` |
| Something upstream — Cloudflare, the platform proxy | a branded page, often with a Ray ID | nothing; it never reached Django |

**Ask which page they saw before doing anything else.** Site nav versus bare white versus
Cloudflare separates the three without touching a server.

## Why 403s used to be invisible

`django.request` was configured at `ERROR`. Django logs 4xx at `WARNING`, so **every 403
and 404 the site ever served was discarded** — a permission denial left no trace at all.
It is now at `WARNING` (`eatart/settings.py`). The cost is bot 404s in the log, which is
worth seeing anyway.

CSRF rejections were always visible, because they come from `django.security.csrf`, a
different logger — but its line says only `Forbidden (CSRF token missing.)`, which cannot
tell the real causes apart.

## What the CSRF handler records, and why

`eatart/views/csrf.py`. The important thing it does is **read the request body in the
failure handler**, which is the only place the distinction can be made:

    CSRF rejected: CSRF token missing. | path=/artist/1044/edit/ | user=17 <someone@example.com>
      | body=empty (arrived with no fields at all) | csrftoken cookie=present
      | content_type=multipart/form-data; boundary=... | content_length=... | ua=...

Read the `body=` clause first:

| `body=` says | Means | Look at |
| --- | --- | --- |
| `unreadable (connection broke…)` | the body died in transit | their connection, or a proxy timeout on a slow upload |
| `empty (arrived with no fields at all)` | it reached us carrying nothing | upstream — a proxy or WAF dropped it |
| `N fields but no token` | the page was rendered or served without the hidden field | our template, or an HTML cache |
| `N fields including the token` | the token was stale or mismatched | an old tab, or a rotated session — the ordinary case |

`django/middleware/csrf.py` catches `UnreadablePostError` and falls through to "token
missing", which is why a broken upload and a genuinely absent token produce the same
Django log line. Only re-reading the body separates them.

The line also carries `cf-ray=...`, which identifies the request in Cloudflare's
**Security → Events**. That is the only way to tell "the visitor's machine sent nothing"
from "our own edge dropped it" — an empty body looks identical either way from inside
Django. If it instead says `no CDN headers`, Cloudflare was not in the path, which
answers the question just as well.

## What the visitor is told

The page branches on the same classification. An **empty body sent with a file attached**
gets the specific message — the browser sent no data, which almost always means it could
not read the attached file, and on a Mac that is iCloud holding it in the cloud with no
local copy. It names Finder's **Download Now**, exporting from Photos, and the screenshot
test as a way to confirm.

Everything else gets the general advice: go Back, reload, retry.

The distinction matters because the two are unrelated faults that arrive at the same
handler. An empty *urlencoded* post looks identical from Django's side but has nothing to
do with files, and telling somebody to re-download a photo they never attached would be
worse than saying nothing — so the file wording is gated on the content type as well.

**Key names only, never values.** The form that lands here most often is an artist
profile, carrying a bio, a phone number, an email and a postal address. Names are enough
to tell the four cases apart; values would put personal data in the log of every failed
submission.

## Things that turned out not to cause it

Worth recording, because both were plausible and both were wrong:

- **A photo too large.** Django accepts a 14 MB upload on these forms, and truncating a
  multipart body mid-file still saves — the token is the first field a browser sends, so
  it survives anything that cuts the tail off. Only a body missing from the very start
  produces "token missing".
- **A crispy layout naming a removed field.** Real (see `ArtworkForm.without_artists`) and
  noisy in the logs, but it warns and drops the field; it never returns 403.
