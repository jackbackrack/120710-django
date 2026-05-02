# Copilot Instructions

## Project Overview

**120710** is an open-source Django gallery management application for [120710.art](https://www.120710.art), an experimental art gallery in Berkeley, CA. It manages artists, artworks, exhibitions (shows), and events, with public-facing pages, a role-based admin workflow, and structured data (Schema.org JSON-LD) on every content page.

## Tech Stack

- **Python 3.11** / **Django 4.2**
- **PostgreSQL** (via `dj-database-url`; TimescaleDB image in Docker)
- **Gunicorn** in production (Railway); Django dev server locally
- **django-allauth** for authentication (including Google OAuth)
- **django-import-export** for CSV/Excel admin import/export
- **Whitenoise** for static files locally; **AWS S3** (`django-storages`) in production
- **Pydantic** for Schema.org type validation in `eatart/schemaorg/`
- **Mailchimp** API for mailing list sync
- **crispy-forms** + Bootstrap 5 for forms
- **docker compose** for local development (services: `db`, `web`, `pgadmin`)

## Repository Layout

```
eatart/             # Django project package
  settings.py       # All config; reads env vars; falls back to SQLite if no POSTGRES_DB
  urls.py           # Root URL configuration
  schemaorg/        # Schema.org JSON-LD layer (types.py, mappers.py, profile.py)
  views/            # Public views (index, about, contact, howto, subscribe)
  services/         # External integrations (mailchimp.py)
  context_processors.py  # navigation_roles injected into every template

gallery/            # Main app
  models/
    artworks.py     # Artwork model — is_public gates public visibility
    exhibitions.py  # Show model — artists M2M, curators M2M, artworks reverse M2M
    events.py       # Event model — FK to Show
    people.py       # Artist model
    tags.py         # Tag model with open-call support
    slugs.py        # Shared slug generation (strip leading/trailing dashes)
  views/            # CBVs + mixins; CanonicalSlugRedirectMixin, StructuredDataMixin
  permissions.py    # visible_artwork_queryset, can_manage_*, role helpers
  admin.py          # ShowAdmin with ArtworkInline + filter_horizontal
  forms.py          # ArtworkForm, ArtistForm, ShowForm, EventForm

accounts/           # User/role management (Artist, Curator, Staff groups)
docker/postgres/    # SQL migration and verification scripts
templates/          # Project-wide templates (base.html, gallery/, account/, public/)
```

## Key Conventions

### Slug Generation
All slugs are generated in `gallery/models/slugs.py` via `build_unique_slug()`. The SQL migration mirrors this logic using `trim(both '-' from regexp_replace(...))` — **always strip leading/trailing dashes** from slugs. URL patterns use `[a-z0-9]+(?:-[a-z0-9]+)*` which rejects any slug with a trailing dash.

### Visibility
`Artwork.is_public` is the sole gate for public artwork visibility. `visible_artwork_queryset()` in `gallery/permissions.py` controls what anonymous/authenticated/curator users see. Artworks assigned to a show via the `shows` M2M are **not** automatically public — `is_public` must be set explicitly (or via the migration SQL's `UPDATE gallery_artwork SET is_public = true`).

### Roles
Three Django groups control access:
- `artist` — can create/edit their own artworks and artist profile
- `curator` — can manage shows and see all artworks
- `staff` — full access (superusers also qualify)

Role helpers live in `gallery/permissions.py`. The `accounts/roles.py` defines the group name constants.

### Schema.org
Every public detail page (artist, artwork, show, event) injects `<script type="application/ld+json">` via the `StructuredDataMixin` in `gallery/views/mixins.py`. Mappers in `eatart/schemaorg/mappers.py` convert Django model instances to Pydantic-validated Schema.org types from `eatart/schemaorg/types.py`. The gallery profile (address, hours, URLs) is centralised in `eatart/schemaorg/profile.py`.

### Admin
`gallery/admin.py` uses `ShowAdmin` with:
- `ArtworkInline` (via `Artwork.shows.through`) to show/edit artworks on a Show
- `filter_horizontal` for `artists`, `curators`, `tags` on Show

### Database
- Local: PostgreSQL via Docker Compose; env vars loaded from `.env.local`
- Production: Railway PostgreSQL; `DATABASE_URL` env var
- Falls back to SQLite if `POSTGRES_DB` is not set (wrong for any real data work)
- Legacy tables are `piece_*`; current tables are `gallery_*`
- Migration SQL lives in `docker/postgres/migrate-piece-to-gallery.sql`

### Media & Static
- Local: filesystem (`MEDIA_ROOT`, `STATIC_ROOT`)
- Production: AWS S3 via `django-storages`; toggled by `USE_S3=True` env var

## Environment Variables

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | Set to `False` to disable debug mode |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT` | PostgreSQL connection |
| `DATABASE_URL` | Alternative full DB URL (Railway injects this) |
| `USE_S3` | `True` to use AWS S3 for media/static |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_STORAGE_BUCKET_NAME` | S3 credentials |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth |
| `MAILCHIMP_API_KEY` / `MAILCHIMP_DATA_CENTER` / `MAILCHIMP_AUDIENCE_ID` | Mailchimp |

## Common Commands

```bash
# Start all services
docker compose --env-file .env.local up

# Run Django migrations
docker compose --env-file .env.local exec web python manage.py migrate

# Run tests
docker compose --env-file .env.local exec web python manage.py test gallery

# Drop and recreate the local database
docker compose --env-file .env.local stop web
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'eatart' AND pid <> pg_backend_pid();"
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "DROP DATABASE eatart;"
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "CREATE DATABASE eatart;"
docker compose --env-file .env.local start web
docker compose --env-file .env.local exec web python manage.py migrate
```

## What Not To Do

- Do not run migrations against SQLite for real data work — always load `.env.local` first
- Do not strip `fix-slugs.sql` back into production — slug fixes are baked into `migrate-piece-to-gallery.sql`
- Do not add `is_public = True` to artwork `save()` automatically — it is an explicit curator decision
- Do not bypass `visible_artwork_queryset` in list/detail views
- Do not add schema.org mappers that call `build_absolute_uri` outside a request context
