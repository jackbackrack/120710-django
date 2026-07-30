# reset.art cutover runbook

Making reset.art the root of the network and 120710.art an entrance that redirects into
`reset.art/site/120710/`. Not started as of 2026-07-30.

The *why* for every decision here is in `docs/reset-art-network.md` — one domain rather than
per-gallery domains, why the `/site/` prefix stays, why the network listings are un-prefixed.
This file is only the order of operations. If the two ever disagree, the design doc is the
record of intent and this one is the record of steps.

**The one rule:** do not turn on the redirects (phase 4) until phase 3 has been verified by
eye. Redirecting a live domain into something unverified is the only way this becomes a bad
afternoon.

## Already done — do not redo

- `/site/<slug>/` scoping for shows, artists, artworks, and their detail pages.
- `/site/<slug>/about|visit|contact|links/`, reading from the `Site` row.
- `current_site` / `default_site` / `info_site` in `eatart/context_processors.py`. The three
  answer different questions; see the design doc before changing any of them.
- `base.html` copes with `current_site` being `None`: brand mark and nav both fall back to
  unscoped. **No template work is needed for the cutover.**
- The root view (`eatart/views/public.py::index`) was never gallery-scoped — it already
  queries every visible show, and `_show_card.html` already names each show's venue. So the
  network home needs no new code, only the identity flip in phase 3.

## Phase 1 — code, deployable now, no visible change

None of this alters what a visitor sees. It can ship days or weeks ahead of the cutover.

- [ ] **Make three settings env-driven** (`eatart/settings.py`): `ALLOWED_HOSTS` (line ~62),
      `CSRF_TRUSTED_ORIGINS` (~64), `GALLERY_DEFAULT_SITE_SLUG` (~165). The slug matters most
      — it turns phase 3 into a variable change revertible in seconds instead of a deploy.
      Add `reset.art` and `www.reset.art` to the host and CSRF lists at the same time.
- [ ] **Delete the `current_site` fallback** in `eatart/context_processors.py` — the
      `elif _DEFAULT_SITE_SLUG: … current_site = default_site` branch.

      This is the change that makes `surl` emit unscoped URLs at the root rather than
      `/site/reset-art/show/foo/`. It is safe to ship before the flip *only if*
      `GALLERY_DEFAULT_SITE_SLUG` is still `120710` — the site then loses 120710's branding at
      the root immediately. **So ship this together with phase 3, not before it.**
- [ ] **Decide about the reset.art row appearing as a venue.** `default_site` requires
      `status=published`, so the row shows up as a card in `/sites/` and as an option in the
      show form's Sites field. No map pin unless it gets coordinates. Either accept that, or
      add a `Site.is_network` flag and exclude it from the site list and the show form.
- [ ] **Canonical tags** on the scoped duplicates: each object is reachable both at
      `/artwork/x/` and `/site/120710/artwork/x/` once the redirects land, and Google should
      be told which is canonical.

## Phase 2 — stand reset.art up alongside 120710.art

- [ ] **Create the reset.art `Site` row.** Staff → Sites → New. Slug `reset-art`, status
      Published, its own About text, description and icon. State `CA` so the time zone
      derives. **No room config, no submission catchment** — it is not a venue.
- [ ] **Add the domains.** Cloudflare DNS → Railway for `reset.art` and `www.reset.art`;
      confirm certificates issue.
- [ ] **Serve both, unredirected.** `GALLERY_DEFAULT_SITE_SLUG` is still `120710` at this
      point, so both domains show identical 120710-branded content. Nothing has changed for
      visitors; you are only proving the host and certificate work.
- [ ] **Move reset.art's nameservers to Cloudflare, at the registrar.** As of 2026-07-30 it is
      still delegated to Gandi (`ns-21-a.gandi.net` and friends) while 120710.art is on
      Cloudflare. Until the registrar is changed, **anything added to a Cloudflare zone for
      reset.art is inert** — the records exist and nothing resolves them. Confirm with
      `dig +short NS reset.art` before adding a single record.
- [ ] **Set up email authentication for reset.art from scratch.** Until this is done,
      `CAMPAIGN_NETWORK_LIST_ENABLED` stays false and the network-wide (reset.art) list
      cannot be mailed — subscribers still accumulate on it, they just cannot be sent to.
      That guard is deliberate; lift it by setting the variable once the records below
      resolve. See `docs/mailing-list.md`. None of 120710.art's records
      carry over: DKIM keys are per-domain and per-provider, and copying them across does not
      work. Verified absent on 2026-07-30 — no DMARC, no Resend DKIM, no `send.reset.art`.
      Get the values from Resend's own domain wizard rather than transcribing 120710.art's:

      * Resend: add reset.art as a sending domain and enter the records it generates — a DKIM
        `TXT` on `resend._domainkey`, an SPF `TXT` and a bounce-feedback `MX` on the
        `send.` subdomain. The apex SPF does **not** need to mention Resend; the envelope
        sender lives on `send.reset.art` and DMARC passes on alignment.
      * smtp2go: add reset.art as a sender domain there too, for transactional mail, and add
        the CNAMEs it asks for.
      * DMARC: a `_dmarc.reset.art` TXT of at least `v=DMARC1; p=none; rua=mailto:…`. Include
        `rua` — without it, monitor mode reports nothing and an authentication problem stays
        invisible until mail is already going to spam.
- [ ] **Point `SITE_BASE_URL` at reset.art** in Railway, in the same change as the identity
      flip. Campaign sends run in a background thread with no request to build URLs from, so
      this is where the unsubscribe link's host comes from — leave it and mail sent after the
      cutover carries 120710.art links. See `docs/mailing-list.md`.

Verify:

```bash
for h in reset.art www.reset.art www.120710.art; do
  printf "%-20s %s\n" "$h" "$(curl -sSL -o /dev/null -w '%{http_code} %{time_total}s' https://$h/)"
done
```

Rollback: remove the domains. Nothing else has changed.

## Phase 3 — flip the identity

One deploy carrying the context-processor change, plus one variable change.

- [ ] Set `GALLERY_DEFAULT_SITE_SLUG=reset-art` in Railway.
- [ ] Deploy the removal of the `current_site` fallback.

Then **verify by eye — no test can tell you the branding looks right:**

- [ ] `reset.art/` — network feed, reset.art brand mark, show cards naming their venues.
- [ ] `reset.art/about/`, `/visit/`, `/contact/`, `/links/` — reset.art's own copy, not
      120710's. If these look empty, the reset.art row has no About text.
- [ ] Show cards at the root link to `/show/<slug>/`, **not** `/site/reset-art/show/<slug>/`.
      If they carry the prefix, the fallback removal did not deploy.
- [ ] `reset.art/site/120710/` — 120710's icon in the navbar, and its nav links scoped.
- [ ] `reset.art/site/120710/about/` — 120710's About, not reset.art's.
- [ ] `reset.art/shows.ics` — `X-WR-CALNAME:Shows and events`; the venue's feed at
      `/site/120710/shows.ics` should name 120710.

Rollback: set `GALLERY_DEFAULT_SITE_SLUG` back to `120710`. The branding returns on restart.
Redeploying the fallback is only necessary if the root looks wrong in a way the slug does not
explain.

## Phase 4 — redirect 120710.art

At **Cloudflare**, as Redirect Rules — not in Django. No deploy, and rollback is a toggle.

- [ ] 301, query strings preserved, for `120710.art`, `www.120710.art` and
      `shows.120710.art`:

```
/artwork/<slug>/              →  reset.art/site/120710/artwork/<slug>/
/artist/<slug>/               →  reset.art/site/120710/artist/<slug>/
/show/<slug>/                 →  reset.art/site/120710/show/<slug>/
/shows/ /artists/ /artworks/  →  reset.art/site/120710/…
/                             →  reset.art/site/120710/
everything else               →  reset.art/<same path>
```

The rewrites into `/site/120710/` are deliberate: printed placard QR codes encode the
unscoped `/artwork/<slug>/`, and a visitor scanning one should land with the gallery's name
and icon on screen rather than on a bare network page. See decision 10 in the design doc.

- [ ] Check a handful of real deep links, including one from a printed placard if you have
      one to hand.

Rollback: disable the rules. 120710.art serves directly again.

## Afterwards

- [ ] **Google Search Console: add reset.art, then file a Change of Address from
      120710.art.** Easy to forget and it is the step that actually preserves rankings.
- [ ] Instagram bio, email signatures, the `LinkTreeEntry` rows (`/links/`).
- [ ] Printed material as it reprints. No rush — the redirects hold.
- [ ] Drop `shows.120710.art` from `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` once its redirect
      has been live long enough to be sure nothing points at it.
- [ ] **Keep 120710.art registered permanently.** It costs nothing and it is the gallery's
      name.

## Things that will bite

- **`GALLERY_DEFAULT_SITE_SLUG = None` is not the change.** An early draft of the design doc
  said so. With `None`, `default_site` resolves to nothing, `info_site` follows, and the four
  public info pages render their empty states on reset.art. It has to *point at the
  reset.art row*, with the fallback removed separately.
- **Order matters between those two.** Removing the fallback while the slug still says
  `120710` strips 120710's branding from the root immediately. Ship them together.
- **Two URLs per object** once the redirects land. Without canonical tags, Google sees
  duplicate content — which is the thing consolidating onto one domain was meant to avoid.
- **Cloudflare sits in front of the app.** It already prepends its Content Signals Policy to
  `/robots.txt`; expect the same kind of interposition elsewhere, and remember that a
  redirect rule there is invisible from the Django side when debugging.
- **Email authentication does not follow the domain.** A verified sending domain is not a
  transferable thing: reset.art needs its own DKIM keys from each provider, and the day the
  identity flips is the day mail starts going out as a domain with no sending reputation at
  all. Warm up with `send_campaign <id> --limit N` across several days rather than putting the
  whole list out at once — see `docs/mailing-list.md`.
- **Delete the leftover Mailchimp DKIM when the account closes.** `k2._domainkey.120710.art`
  and `k3._domainkey.120710.art` still CNAME to `dkim2/dkim3.mcsv.net`. Harmless while the
  account is kept read-only, but there is no reason to keep authorizing a provider you left.
- **`shows.120710.art` is in `ALLOWED_HOSTS` today.** It resolves and serves, so it needs a
  redirect rule of its own or it becomes the one door left open.
