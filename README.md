# 120710

Open-source Django gallery management application for [120710.art](https://www.120710.art), an experimental art gallery at 1207 Tenth Street, Berkeley, CA.

Manages artists, artworks, exhibitions (shows), events, and juror reviews/ratings with public-facing pages, a role-based admin workflow, a mailing list of its own, and Schema.org JSON-LD structured data on every content page.

---

## Features

- **Artists** — profile pages with bio, statement, image, website, Instagram, and linked artworks
- **Artworks** — detail pages with medium, dimensions, pricing, Schema.org `VisualArtwork` structured data
- **Shows (Exhibitions)** — M2M artists and curators, artworks inline, open-call support, Schema.org `VisualArtsEvent`
- **Events** — linked to shows with date/time, Schema.org `VisualArtsEvent` with `superEvent`
- **Reviews and ratings** — per-show juror assignments with one 1..5 rating/review per juror-artwork-show
- **Tags** — filterable across all content types; special `Open Call` tag for open-call submissions
- **Role-based access** — `artist`, `curator`, `juror`, `staff` groups with granular permissions
- **Google OAuth** via django-allauth
- **Admin** — ShowAdmin with artwork inline and filter_horizontal for artists/curators/tags; CSV/Excel import-export on all models
- **Schema.org JSON-LD** — Pydantic-validated structured data on every public detail page
- **Event RSVPs** — yes/maybe/no on an event page, with a reminder the day before that is the reason for asking; see [docs/event-rsvps.md](docs/event-rsvps.md)
- **Visit booking** — visitors pick a slot from the venue's structured opening hours and the gallery gets a calendar invitation by email; no Google integration to configure — see [docs/visit-booking.md](docs/visit-booking.md)
- **Deploying** — migrations and static uploads run in Railway's pre-deploy phase, so the old container keeps serving; migrations must stay backwards-compatible, and there is a `/healthz` that touches nothing — see [docs/railway-deploys.md](docs/railway-deploys.md)
- **Site directors** — an admin for one venue and nothing beyond it: shows, curation, jurying, pickups, its artists and artworks, its own settings and bookings; see [docs/site-directors.md](docs/site-directors.md)
- **Image colour** — derived images are converted to sRGB through the source's own ICC profile, so an Adobe RGB photograph of an artwork does not shift; changing any image spec needs `manage.py generateimages` in the same deploy — see [docs/image-colour.md](docs/image-colour.md)
- **Diagnosing a 403** — which of three unrelated faults a "Forbidden" report actually was, from one log line; see [docs/diagnosing-403s.md](docs/diagnosing-403s.md)
- **Mailing list** — subscribers, campaigns and unsubscribes in our own database; campaigns render from MJML and send via Resend, while transactional mail stays on smtp2go. Sends run in the background and a send that stops part-way can be resumed without mailing anyone twice — see [docs/mailing-list.md](docs/mailing-list.md)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Framework | Django 4.2 |
| Database | PostgreSQL (TimescaleDB image in Docker) |
| Auth | django-allauth (local + Google OAuth) |
| Forms | crispy-forms + Bootstrap 5 |
| Structured data | Pydantic Schema.org types |
| Static files (local) | Whitenoise |
| Media/static (production) | AWS S3 via django-storages |
| Deployment | Railway (Gunicorn) |
| Local dev | Docker Compose (`db`, `web`, `pgadmin`) |

---

## Getting Started

### Prerequisites

- Docker + Docker Compose
- A `.env.local` file (see [Environment Variables](#environment-variables) below)

### Start the stack

```bash
docker compose --env-file .env.local up
```

This starts:
- `db` — PostgreSQL (TimescaleDB) on port 5432
- `web` — Django dev server on port 8000
- `pgadmin` — pgAdmin on port 5050

### Apply migrations

```bash
docker compose --env-file .env.local exec web python manage.py migrate
```

### Create a superuser

```bash
docker compose --env-file .env.local exec web python manage.py createsuperuser
```

### Run tests

```bash
docker compose --env-file .env.local exec web python manage.py test gallery
```

---

## Environment Variables

Copy `.env.local.example` (if present) or create `.env.local` with:

```env
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
POSTGRES_DB=eatart
POSTGRES_USER=eatart
POSTGRES_PASSWORD=eatart
POSTGRES_HOST=db
POSTGRES_PORT=5432

# Optional: Google OAuth
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Optional: reCAPTCHA (enabled automatically only if both keys exist)
RECAPTCHA_PUBLIC_KEY=
RECAPTCHA_PRIVATE_KEY=
# Optional explicit override (True/False)
RECAPTCHA_ENABLED=

# Transactional mail (production only). Read by the smtp2go library from the environment
# directly, not via settings.py — so it shows up in no Django setting.
SMTP2GO_API_KEY=

# Optional: Resend, for mailing-list campaigns only
RESEND_API_KEY=
RESEND_SIGNING_SECRET=

# Absolute base for links in campaign mail. Campaign sends run in a background thread with
# no request to build URLs from, so this is where the unsubscribe link's host comes from.
SITE_BASE_URL=https://www.120710.art
# Set false to send campaigns inline rather than in a background thread (the tests do this)
CAMPAIGN_SEND_IN_BACKGROUND=
# Messages a second when sending a campaign. One API request per message, so this is the
# provider's rate limit — Resend allows about two a second by default.
CAMPAIGN_MESSAGES_PER_SECOND=2
# Allow sending to the network-wide (reset.art) list. Off until reset.art has its own DKIM
# and SPF; subscribers can be collected onto it meanwhile, just not mailed.
CAMPAIGN_NETWORK_LIST_ENABLED=false

# Optional: AWS S3 (set USE_S3=True to enable)
USE_S3=False
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_STORAGE_BUCKET_NAME=
```

---

## Repository Layout

```
eatart/             # Django project package
  settings.py       # Config; reads env vars; falls back to SQLite if no POSTGRES_DB
  urls.py           # Root URL configuration
  schemaorg/        # Schema.org JSON-LD layer
    types.py        # Pydantic types: Person, VisualArtwork, VisualArtsEvent, ArtGallery
    mappers.py      # Model → Schema.org converters
    profile.py      # GALLERY_PROFILE — address, hours, social links
  views/            # Public views: index, about, contact, howto, subscribe

gallery/            # Main gallery app
  models/
    artworks.py     # Artwork — is_public gates public visibility
    exhibitions.py  # Show — artists M2M, curators M2M, artworks reverse M2M
    events.py       # Event — FK to Show
    people.py       # Artist — linked to auth.User via FK
    tags.py         # Tag — open-call support
    slugs.py        # build_unique_slug() — shared slug generation
  views/            # CBVs + mixins (CanonicalSlugRedirectMixin, StructuredDataMixin)
  permissions.py    # visible_artwork_queryset, can_manage_*, role predicates
  admin.py          # ShowAdmin with ArtworkInline + filter_horizontal
  forms.py          # ArtworkForm, ArtistForm, ShowForm, EventForm

accounts/           # User/role management
reviews/            # Juror assignments and artwork review/rating workflows
scripts/    # SQL migration and verification scripts
templates/          # Project-wide templates (base.html, gallery/, account/, public/)
```

---

## Data Model

```
Tag <──M2M── Artist, Artwork, Show, Event

Artist ──M2M──> Show.artists        (artist.shows)
Artist ──M2M──> Show.curators
Artist <──M2M── Artwork.artists     (artist.artworks)

Show <──FK── Event.show             (show.events, CASCADE)
Show <──M2M── Artwork.shows         (show.artworks)

User ──FK──> Artist.user            (user.artists)
User ──FK──> Show.managing_curator  (user.managed_shows)
User ──FK──> Artwork.created_by

Show <──FK── ShowJuror.show         (show.jurors)
User <──FK── ShowJuror.user         (user.juror_assignments)

Show <──FK── ArtworkReview.show     (show.reviews)
Artwork <──FK── ArtworkReview.artwork (artwork.reviews)
User <──FK── ArtworkReview.juror    (user.artwork_reviews)
```

### Visibility

`Artwork.is_public` is the sole gate for public artwork visibility. Being assigned to a show does not make an artwork public — it must be set explicitly by a curator.

`gallery/permissions.py::visible_artwork_queryset(user)`:
- Staff/curators: see all artworks
- Authenticated users: see public artworks + their own
- Anonymous: see only `is_public=True`

---

## Schema.org Structured Data

Every public detail page includes a `<script type="application/ld+json">` block. Mappers in `eatart/schemaorg/mappers.py` convert model instances to Pydantic-validated Schema.org types:

| Page | Schema.org type |
|---|---|
| Artist detail | `Person` |
| Artwork detail | `VisualArtwork` |
| Show detail | `VisualArtsEvent` with `workFeatured` and `performer` |
| Event detail | `VisualArtsEvent` with `superEvent` pointing to the show |
| Homepage/about | `ArtGallery` |

The gallery's address, hours, and contact details are centralised in `eatart/schemaorg/profile.py`.

---

## Roles

| Group | Capabilities |
|---|---|
| `artist` | Create and edit own artworks and artist profile |
| `curator` | Manage shows, events, and see all artworks |
| `juror` | Review assigned-show artworks with 1..5 ratings and notes |
| `staff` | Full access |

Superusers bypass all role checks. Group name constants are in `accounts/roles.py`.

Role behavior notes:
- Promoting a linked artist account to `curator` sets the associated artist profile to public (`Artist.is_public=True`).
- Edit/Delete links in artist, artwork, show, and event list/detail pages are permission-gated and only shown when the current user can manage the record.

---

## Database Migration (Legacy → Current)

The original database used `piece_*` tables. The migration to `gallery_*` is handled via SQL scripts in `scripts/`:

| Script | Purpose |
|---|---|
| `migrate-piece-to-gallery.sql` | Full transactional migration |
| `verify-piece-to-gallery.sql` | Row count and missing-record checks |

Run both in pgAdmin (or `psql`) after restoring the legacy dump and running Django migrations. See `database_migration_runbook.md` for the full workflow.

Key migration decisions:
- `gallery_show_artists` is derived from artwork membership — any artist with an artwork in a show becomes an artist of that show
- All imported artworks are set `is_public = true`
- Slugs have leading/trailing dashes stripped (matching Django's `build_unique_slug`)

---

## Deployment (Railway)

Production is hosted on [Railway](https://railway.app). Railway injects `DATABASE_URL` automatically. Set all required env vars in the Railway project settings.

Static files and media are served from AWS S3 when `USE_S3=True`.

---

## Contributing

This is an open-source project. Contributions, bug reports, and feature requests are welcome.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes with tests where appropriate
4. Run the test suite: `docker compose --env-file .env.local exec web python manage.py test gallery`
5. Open a pull request

---

## License

See [LICENSE](LICENSE).
