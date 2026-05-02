# Agents

This file describes the AI agent context, workflow guidance, and known patterns for working with the **120710** codebase. It is intended to help GitHub Copilot agents and other AI tools understand the project deeply enough to make safe, idiomatic changes.

---

## Project Summary

120710 is a Django 4.2 gallery management system for an experimental art gallery in Berkeley, CA. It is open-source and deployed on Railway with PostgreSQL. The primary data entities are **Artist**, **Artwork**, **Show** (exhibition), **Event**, **Tag**, plus show-level **Juror assignments** and **Artwork reviews/ratings**.

---

## Architecture

### Apps

| App | Purpose |
|---|---|
| `gallery` | Core models, views, forms, admin, permissions, URL routing |
| `accounts` | User registration, role assignment, profile editing |
| `reviews` | Juror assignment, show review dashboards, artwork review/rating workflow |
| `eatart` | Django project package: settings, root URLs, schema.org, public views, services |

### Key files

| File | Role |
|---|---|
| `gallery/models/artworks.py` | `Artwork` — `is_public` gates visibility |
| `gallery/models/exhibitions.py` | `Show` — M2M to `Artist` (artists + curators), reverse M2M from `Artwork.shows` |
| `gallery/models/people.py` | `Artist` — linked to `auth.User` via FK |
| `gallery/models/events.py` | `Event` — FK to `Show` |
| `gallery/models/slugs.py` | `build_unique_slug()` — shared slug logic; always strips leading/trailing dashes |
| `gallery/permissions.py` | `visible_artwork_queryset()`, `can_manage_*()`, role predicates |
| `gallery/admin.py` | `ShowAdmin` with `ArtworkInline` and `filter_horizontal` for artists/curators/tags |
| `gallery/views/mixins.py` | `CanonicalSlugRedirectMixin`, `StructuredDataMixin` |
| `reviews/models.py` | `ShowJuror`, `ArtworkReview` — show assignment + 1-5 juror scoring |
| `reviews/views.py` | Curator/juror review dashboard, juror assignment, juror submit/edit, curator edit |
| `reviews/urls.py` | `/show/<show>/reviews/...` routes |
| `eatart/schemaorg/types.py` | Pydantic Schema.org types: `Person`, `VisualArtwork`, `VisualArtsEvent`, `ArtGallery` |
| `eatart/schemaorg/mappers.py` | Convert model instances → Schema.org objects |
| `eatart/schemaorg/profile.py` | `GALLERY_PROFILE` dict — gallery name, address, hours, social links |

---

## Data Relationships

```
Tag <──M2M── Artist, Artwork, Show, Event

Artist ──M2M──> Show.artists       (artist.shows reverse)
Artist ──M2M──> Show.curators      (no reverse name set)
Artist <──M2M── Artwork.artists    (artist.artworks reverse)

Show <──FK── Event.show            (show.events reverse, CASCADE)
Show <──M2M── Artwork.shows        (show.artworks reverse)

User ──FK──> Artist.user           (user.artists reverse)
User ──FK──> Show.managing_curator (user.managed_shows reverse)
User ──FK──> Event.managing_curator
User ──FK──> Artwork.created_by

Show <──FK── ShowJuror.show        (show.jurors reverse)
User <──FK── ShowJuror.user        (user.juror_assignments reverse)

Show <──FK── ArtworkReview.show    (show.reviews reverse)
Artwork <──FK── ArtworkReview.artwork (artwork.reviews reverse)
User <──FK── ArtworkReview.juror   (user.artwork_reviews reverse)
```

### Important distinctions
- `Show.artists` — the M2M of featured artists for a show (populated by migration from artwork membership)
- `Show.curators` — the M2M of curating artists (populated directly from legacy data)
- `Artwork.shows` — the M2M of shows an artwork belongs to (source of truth for artwork-show linkage)
- `Artwork.is_public` — **sole gate** for public visibility; being in a show does not imply `is_public=True`
- `ShowJuror` — authoritative assignment table for who can review a specific show
- `ArtworkReview` — one review per `(show, artwork, juror)`; rating must remain in `1..5`

---

## Slug Rules

All slugs follow the pattern `[a-z0-9]+(?:-[a-z0-9]+)*`:
- No leading or trailing dashes
- No consecutive dashes
- Unique within the model (suffix `-2`, `-3`, etc. appended when needed)

The Python implementation is `gallery/models/slugs.py::build_unique_slug()`.
The SQL implementation in `migrate-piece-to-gallery.sql` uses:
```sql
trim(both '-' from regexp_replace(
    regexp_replace(lower(trim(COALESCE(name, 'fallback'))), '[^a-z0-9 -]', '', 'g'),
    '[ -]+', '-', 'g'
))
```

**Never write a slug generator that doesn't strip leading/trailing dashes.**

---

## Visibility Rules

`gallery/permissions.py::visible_artwork_queryset(user)` returns a `Q()` object:
- Curators and staff see everything (`Q()` — no filter)
- Authenticated users see public artworks + their own
- Anonymous users see only `is_public=True`

Always apply this filter via `.filter(visible_artwork_queryset(request.user)).distinct()`.  
Never query `Artwork.objects.all()` in a public-facing view.

## Reviews and Jurors

Review permissions live in `gallery/permissions.py`:
- `is_juror_for_show(user, show)` checks assignment through `show.jurors`
- `can_view_reviews(user, show)` allows show managers/staff or assigned jurors

Workflow in `reviews/views.py`:
- `show_review_dashboard`:
    - Curator/staff: all show artworks with `avg_rating` and `review_count`, all reviews, assigned jurors
    - Juror: only their own reviews and pending artworks for that show
- `artwork_review`:
    - Juror creates/updates their own review record for a show-artwork pair
    - Curator/staff sees all juror reviews for that artwork in-show
- `curator_edit_review`: curator/staff edits a juror's review
- `show_juror_assignment`: curator/staff assigns/removes jurors for a show

Model constraints in `reviews/models.py`:
- `ShowJuror.unique_together = ('show', 'user')`
- `ArtworkReview.unique_together = ('show', 'artwork', 'juror')`
- `ArtworkReview.rating` is integer-validated to 1..5
- `ShowJuror.save()` applies the `juror` role via `add_juror_role(user)`

---

## Schema.org Layer

Every public detail page includes structured data via `StructuredDataMixin`. The mixin calls the `schema_mapper` class attribute on the view with `(instance, request)` and serialises the result to JSON-LD.

Mapper functions live in `eatart/schemaorg/mappers.py`:

| Mapper | Schema.org type | Used on |
|---|---|---|
| `artist_to_schema(artist, request)` | `Person` | Artist detail |
| `artwork_to_schema(artwork, request)` | `VisualArtwork` | Artwork detail |
| `show_to_schema(show, request)` | `VisualArtsEvent` | Show detail |
| `event_to_schema(event, request)` | `VisualArtsEvent` | Event detail |
| `gallery_to_schema(request)` | `ArtGallery` | Homepage/about |

Rules:
- `build_absolute_url(request, path)` must be used for all URLs — never hardcode or call `build_absolute_uri` outside a request context
- `build_absolute_media_url(request, field_file)` handles media files safely
- Mappers must never raise; all fields are optional unless the Schema.org spec requires them
- `dump_json_ld(data)` uses `ensure_ascii=True` to be safe in all environments
- `build_graph(items)` produces a `@graph` array for pages with multiple schema objects

---

## Admin

`ShowAdmin` (in `gallery/admin.py`) is the reference implementation for how to customise admin in this project:
- Inline models use the M2M `.through` table, not a FK
- `filter_horizontal` is used for all M2M fields on Show
- All model admins inherit from `ImportExportAdmin` to support CSV/Excel import/export

---

## Database Migration (Legacy → gallery_*)

The migration from the legacy `piece_*` schema to `gallery_*` is handled by:

| File | Purpose |
|---|---|
| `docker/postgres/migrate-piece-to-gallery.sql` | Full transactional migration SQL |
| `docker/postgres/verify-piece-to-gallery.sql` | Row count and missing-record verification |

Key migration behaviours:
- `gallery_show_artists` is **derived** from artwork membership (no legacy source table)
- All imported artworks are set `is_public = true`
- Slugs have leading/trailing dashes stripped in the SQL (matching Python `build_unique_slug`)
- Sequences are reset after all INSERTs

The migration is run via pgAdmin (not the shell script) to match the Railway production workflow where `psql` CLI access is not available.

---

## Roles

| Group | Capabilities |
|---|---|
| `artist` | Create/edit own artworks and artist profile |
| `curator` | Manage shows, see all artworks, manage events |
| `juror` | Submit reviews/ratings for assigned shows |
| `staff` | Full access (equivalent to superuser) |

Group names are constants in `accounts/roles.py`. Always use those constants, never hardcode the string `"curator"` etc.

---

## What Not To Do

- Do not auto-set `is_public = True` in `Artwork.save()` — visibility is an explicit curator decision
- Do not bypass `visible_artwork_queryset` in any list or detail view
- Do not add schema.org mappers that call `build_absolute_uri` without a `request` argument
- Do not write slug logic without stripping leading/trailing dashes
- Do not run Django management commands without loading `.env.local` locally — the default DB is SQLite
- Do not add `M2M` reverse traversals without `.distinct()` to avoid duplicate rows
- Do not add `Show.artists` population logic to `Artwork.save()` — it is derived in the migration only
- Do not bypass `can_view_reviews` / `is_juror_for_show` in review pages
- Do not remove uniqueness constraints for show jurors or juror reviews without explicit migration planning
- Do not change review rating bounds (1..5) in forms/models without coordinated model, form, template, and reporting updates
