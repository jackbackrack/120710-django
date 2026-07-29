# reset.art as the network root

How the single gallery at 120710.art generalises into a network of galleries under
reset.art. Design agreed 2026-07-29. The cutover itself is **not done** and `/near/` is
deliberately deferred until there is a second gallery to browse; the scoped list convergence
under "Already built" did land.

## The goal, and why it rules out per-gallery domains

reset.art is the non-profit; 120710.art is one gallery. The point of the `Site` facility is
to replicate 120710 rather than to host it, and the value of doing that on one platform is
**network effects**: one artist account across every gallery, a visitor at one gallery
discovering the others, one open-call listing an artist can browse.

The tempting architecture — give each gallery its own domain, resolved from the `Host`
header — defeats exactly that. Separate registrable domains mean separate cookie jars and
therefore separate sessions, separate SEO authority instead of one compounding domain, and
a visitor who never learns the other galleries exist. It was considered and rejected. Its
one real advantage, a gallery keeping its own address bar, is worth less than the network.

So: **one domain, one session, one accumulating body of SEO authority.** A gallery's
identity lives in its `Site` page, its branding in the navbar when scoped to it, and its
vanity domain as a permanent entrance that redirects in.

## What is already network-shaped

Most of this is done, which is why the remaining work is small:

- **`Artist` and `Artwork` have no `Site` foreign key at all.** They are global. One artist,
  one profile, one body of work, visible network-wide. Verified by inspecting
  `_meta.get_fields()` — there is no site relation on either model.
- **`Show.sites` is many-to-many**, so a show spanning two venues, or a network-wide show,
  is already expressible.
- **The un-prefixed listings are the network views.** `/artists/`, `/artworks/` and
  `/shows/` return identical results whether `GALLERY_DEFAULT_SITE_SLUG` is `'120710'` or
  `None` — measured, not assumed. The setting changes only the chrome (navbar branding, and
  which URLs `surl` generates), never the content.
- **The root view is already a network feed.** `eatart/views/public.py::index` queries every
  visible show, not one site's. It looks like 120710's home page today only because every
  show in the database happens to be 120710's.
- **Show cards already attribute their venue.** `_show_card.html` names and links each of a
  show's sites, suppressed only by `hide_site=True` on the site detail page, where it would
  be redundant. So a network feed already tells a visitor which gallery a show is at.

## Decisions

**1. reset.art is canonical. 120710.art 301s to it, preserving paths.**
Consolidating link equity into one domain is a feature here, not a cost: every gallery that
joins compounds one domain's authority rather than diluting across N. Keep 120710.art
registered and pointed forever — it costs nothing and it is the gallery's name.

**2. `GALLERY_DEFAULT_SITE_SLUG = None` on reset.art.**
Today it pins every unscoped page to 120710's context, which would make the network home
wear one gallery's name. Low blast radius: it changes branding, not data (see above).

**3. Sites keep the `/site/<slug>/` prefix. Do not promote gallery slugs to the root.**
A prefix earns its place by protecting against *unbounded, user-generated* names. Gallery
slugs are user-generated, and 24 top-level segments are already taken — `artists`, `shows`,
`about`, `visit`, `collection`, `contact` and the rest — so a gallery slugged `visit` would
shadow a real route. `reset.art/120710/` was considered and rejected for this reason.

**4. Network listings stay un-prefixed. The symmetry lives in the code, not the URL.**

```
/artists/  /artworks/  /shows/            the network
/site/120710/artists/                     one venue
/near/120710/shows/?radius=25             a geography
```

Collections (`artists`, `artworks`, `shows`, `events`, `collectors`) are a bounded set the
developer controls, so there is no collision to defend against and no prefix is warranted.
The asymmetry is principled rather than accidental: the namespace does its job in exactly
the one place that needs it.

Prefixing the network views for symmetry (`/network/artists/`) was considered and rejected
on three grounds: the default case should have the shortest URL, since it is the one that
gets printed on cards and in grant applications; it would touch 172 `get_absolute_url` call
sites; and printed placard QR codes encode `/artwork/<slug>/` via `build_absolute_uri`,
so those are objects in the world that cannot be reissued. It also partly defeats itself —
`/site/120710/artwork/x/` is not a *different* artwork, so a network prefix would be a third
URL for one object, which is the duplicate-canonical problem this whole move avoids.

Instead, get the uniformity where it pays: **a scope resolver** where "no prefix" is simply
the network scope.

```
/artists/                 -> NetworkScope()
/site/120710/artists/     -> SiteScope(site)
/near/120710/shows/       -> RadiusScope(site, miles)
```

Listing views take a scope and ask it to filter, with no special-casing of "no scope".
`eatart/context_processors.py` is already a degenerate version of this — it resolves
`current_site` from the path — and is the natural place for it to grow.

Revisit if collections ever become user-generated (a gallery defining its own top-level
collection type). Then the bounded-set premise fails and prefixing is right — better at 172
call sites than at 1,700.

**5. `/near/` centres on a venue, never on arbitrary coordinates.**

```
/near/120710/shows/          within the default radius of 120710
/near/120710/artworks/
```

`Site.latitude`/`longitude` already exist, and the table has dozens of rows even at scale,
so this is a haversine sweep in Python with no spatial index and no new data.
`haversine_miles` currently lives in `gallery/management/commands/set_site_catchment.py`
and wants moving somewhere importable (`gallery/geo.py`) once a view needs it.

Arbitrary lat/lng in the URL was rejected: it needs a geocoder on every request and gives
crawlers an infinite coordinate space to enumerate.

Note there are two distinct geographic questions and only this one is cheap. "Which artists
are *based* near here?" needs ZCTA centroids at request time, and `load_centroids()` reads
`.zcta_cache/`, which is gitignored and does not exist on the deployment. That question is
already answered offline by `manage.py set_site_catchment`, which stores the *result* as a
postal-code list on the `Site`. Do not build a web feature on the cache.

**6. Radius is a query parameter from a bounded menu.**

```
/near/120710/shows/                 canonical, default radius
/near/120710/shows/?radius=50
```

10 / 25 / 50 / 100 miles, with anything else falling back to the default. The radius is a
*filter*, not a scope, so it belongs in the query string: in the path, every radius becomes
its own indexable URL showing largely overlapping content, which is the duplicate-content
problem again.

The bounded menu is not cosmetic. Anonymous card grids are fragment-cached on the filter:

```django
{% cache anon_grid_cache_seconds artwork_cards active_tag page_obj.number %}
```

Radius has to join that key alongside `active_tag` and `site.pk`, or a signed-out visitor
asking for 50 miles is served a cached 25-mile grid. Five bounded values keep the cache
small; arbitrary integers would blow it up.

**A test will not catch this by default.** `eatart/settings.py` forces `DummyCache` whenever
`'test'` is in `sys.argv`, so cached fragments cannot leak between tests — which also means
no test exercises the grid cache unless it opts back in with `override_settings(CACHES=…)`.
`SiteFeatureTests.test_scoped_and_network_grids_do_not_share_a_cache_entry` is the pattern;
it was written first without the override, passed, and proved nothing.

**7. `/near/<site>/artists/` means "artists *showing* near here", not "based near here".**
The only genuinely ambiguous case — shows and artworks have no such reading. The network is
for discovery, so the visitor's question wins. The curator's question ("is this submitter
local?") already has a home on the Submissions page via `submission_scope`.

**8. UI: a pill row plus venue chips.**

```
Shows within   [10]  ( 25 )  [50]  [100]  miles of 120710

4 galleries in range:  120710 · Adobe Books · Pro Arts · Royal NoneSuch
```

Pills are plain links to the same page with a different `?radius=`, matching the existing
`?area=out` toggle on the Submissions page — no JavaScript, works signed out, back button
behaves. A `<select>` submitting on change (as the card-size control does) is the
alternative, but pills show the available range at a glance.

The venue chips are not decoration. A radius with no indication of which venues it caught is
opaque — a visitor cannot judge whether 25 miles is the right number without seeing what it
includes — and the chips are themselves the network-discovery surface that justifies the
feature. Later, the natural home for all of this is the map already on `/sites/`: draw the
circle, highlight the pins inside it.

## Deferred, and why

`/near/` is not built. **The network currently has one node**, so it would return only
120710's own shows and could be neither demonstrated nor screenshotted, though it is
perfectly testable with fixtures. Build it when gallery #2 lands; decisions 5–8 are the
specification.

## Consequences to handle at cutover

- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` are hard-coded lists (`eatart/settings.py:62`,
  `:64`) and want to become env-driven, with reset.art added.
- Every absolute URL in the app comes from `request.build_absolute_uri()` — emails,
  invitations, placard QR codes. There is no hard-coded domain in app code, so links follow
  whichever host served the request and no email code needs touching.
- Sign-in is per-domain, which is the whole reason for a single domain. Nothing may bounce
  an artist between hosts mid-flow.
- A `<link rel="canonical">` on `?radius=` variants pointing at the bare URL.

**9. `reset.art/` is the network show feed that `index` already renders.**
Hero on the current show from any gallery, then Current / Upcoming / Past, with each card
naming its venue — which `_show_card.html` already does. `/shows/` remains the full
paginated list. This needs **no new code**: flipping `GALLERY_DEFAULT_SITE_SLUG` to `None`
(decision 2) is the whole change, because `index` was never gallery-scoped in the first
place.

**10. Deep links from 120710.art rewrite into the gallery's scope.**

```
120710.art/artwork/foo/  ->  reset.art/site/120710/artwork/foo/
120710.art/show/bar/     ->  reset.art/site/120710/show/bar/
```

So a visitor scanning a printed placard — which encodes the unscoped `/artwork/<slug>/` via
`build_absolute_uri` — arrives with the gallery's name and icon in the navbar, rather than on
a bare network page. Requires `<link rel="canonical">` on the scoped variants, since each
object is then reachable at two URLs.

**Every collection the rewrite needs has a scoped equivalent:**

| unscoped | scoped equivalent |
|---|---|
| `/show/<slug>/` | `/site/<s>/show/<slug>/` |
| `/artist/<slug>/` | `/site/<s>/artist/<slug>/` |
| `/artwork/<slug>/` | `/site/<s>/artwork/<slug>/` |
| `/shows/` | `/site/<s>/shows/` |
| `/artists/` | `/site/<s>/artists/` |
| `/artworks/` | `/site/<s>/artworks/` |

Anything else (`/events/`, `/collection/`, `/howto/`, `/about/`) is network-level by nature
and redirects unscoped.

An earlier draft of this document claimed the last two had no route. They did — they were
simply registered with a `slug` kwarg rather than `site_slug`, so a search for `site_slug`
missed them. What was true is that they were **thin copies that had drifted**, which is a
worse problem than a missing route and is now fixed; see "Already built" below.

**11. Retire `shows.120710.art`.**
Redirect it to reset.art alongside the apex and `www`, then drop it from `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` (`eatart/settings.py:62`, `:64`) at cutover.

## Already built

**The scoped list convergence** (2026-07-29). `/site/<slug>/artists/` and
`/site/<slug>/artworks/` existed but as separate `DetailView`s over their own 15-line
templates, and they had drifted into thinner pages than the network lists: no pagination —
they rendered *every* artist at the venue in one response — no tag filter, no count, no
per-card permissions, and no anonymous fragment cache.

They now route to `ArtistListView` / `ArtworkListView` with a `site_slug` kwarg, exactly as
`/site/<slug>/shows/` routes to `ShowListView`, which is what its docstring already
recommended: "reuses this view and its template so a venue's list is the same page with the
same controls rather than a thinner copy that drifts." Two views and two templates deleted.

Details worth remembering:

- `Artist` has no relation to `Site`, so a venue's artists are found through the work:
  `artworks__shows__sites=site`. Artworks are `shows__sites=site`.
- Both models already order by `['-created_at']`, which is what the deleted views ordered by
  explicitly, so the convergence changed no ordering.
- `visible_site_or_404` (in `gallery/views/mixins.py`) keeps drafts hidden from non-staff.
  Deliberately stricter than
  `ShowListView`'s plain `get_object_or_404`: these two URLs already behaved that way and
  loosening it would quietly make an unpublished venue browsable.
- `site.pk` joined the `{% cache %}` key on both card fragments. Without it the two grids
  share one entry and a signed-out visitor to a venue sees the network's artists.
- The scoped context object is `artist_list` / `artwork_list` now, not `artists` / `artworks`.

**The public info pages** (2026-07-29). Info, Visit, Contact and Links were hard-coded
templates naming one gallery — `contact` and `visit` were literally
`render(request, 'public/contact.html')` with no context at all. They now read from the
`Site`, so a second gallery gets its own without a deploy.

Decisions taken while building it:

- **`default_site` is a third context variable, not a reuse of `current_site`.** The
  temptation is to point `GALLERY_DEFAULT_SITE_SLUG` at the reset.art row and let
  `current_site` fall back to it. That breaks `surl`, which scopes URLs whenever
  `current_site` is set — every show card on the network home would link to
  `/site/reset-art/show/foo/`. So: `current_site` is what the URL is scoped to (None at the
  network level), `default_site` is the deployment's own identity, and `info_site` is
  `current_site or default_site` — what the four pages read.
- **The existing `current_site` fallback is untouched.** Removing it is cutover step 2, and
  doing it early would strip 120710's branding from the live site. `info_site` is correct
  before and after, which is the point of introducing it separately.
- **Fallback is per page, not per field** (as agreed). A venue with no phone shows no phone
  rather than the umbrella's. The one exception is About, which falls back to
  `description`, because a venue with neither has nothing to say at all.
- **Rich text, not structured models.** `Site.about` holds mission, story and people as one
  field. `SitePerson` / `SitePressMention` were considered and deferred: structure earns its
  keep at the second gallery with staff to list. Needed a second template filter,
  `sanitize_rich`, because the existing `sanitize` allowlist strips `table`, `h1`, `h2` and
  `img`. Kept separate rather than widening `sanitize`, since that one governs
  artist-editable bios where `<img>` would permit tracking pixels from anyone with an
  account. nh3 still strips event handlers and dangerous schemes in both.
- **Links carry a nullable site.** Null means network-level and appears on every venue's
  page; a venue's own links carry its site, so joining the network does not mean inheriting
  another gallery's link list. `LinkTreeEntry` had zero rows, so there was nothing to
  migrate.
- **Subscribing was left alone**, per the decision to move mailing lists onto the site
  itself later. Contact only advertises a list when `MAILCHIMP_AUDIENCE_ID` is set, rather
  than pointing every venue at one shared audience.
- **The map is generated from the venue's own coordinates** instead of the committed
  `120710-map.png`. `120710-street-view.png` becomes the optional `visit_image` upload.

Two bugs found while testing it:

- The scoped pages leaked drafts. The context processor resolves a site from the path
  *without* checking status, so `/site/<draft>/about/` rendered an unpublished venue's copy
  to the public. The four views now call `visible_site_or_404` themselves.
- `{% if info_site.formatted_address %}` never fired, because `country` defaults to `US` and
  is therefore always set — a venue with no address would have printed its name and
  "United States of America". Guarded on `street or city` instead.

`0070_seed_120710_public_info` carries 120710's existing wording into its `Site` row. Without
it the deployed pages would come back blank, since at that point the content exists only in
git history. Every write is guarded on the field being empty, so it is safe to re-run and
will not overwrite anything edited through the site form afterwards.

## Cutover order

1. Make `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` env-driven; add reset.art in Railway with
   its certificate.
2. Serve both domains, unredirected, and check reset.art renders correctly while
   `GALLERY_DEFAULT_SITE_SLUG` is still `'120710'`.
3. Set it to `None`. reset.art becomes the network home; verify the navbar loses 120710's
   branding at the root and keeps it under `/site/120710/`.
4. Turn on the 301s from 120710.art (with the scope rewrites) and `shows.120710.art`.
5. Canonical tags on scoped duplicates.

Step 3 is the one to verify by eye rather than by exit code: it is a branding change with no
test that can tell you it looks right.
