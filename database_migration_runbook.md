# Database Migration Runbook

## Purpose

This runbook documents the safest path to migrate data from the legacy `piece_*` schema into the new `gallery_*` schema without data loss.

It assumes:

- the old database dump contains the legacy `piece_*` tables
- the current codebase uses the new `gallery` app and migrations
- PostgreSQL is running through Docker Compose
- Django commands are run with PostgreSQL environment variables loaded, not against the default SQLite fallback
- That you've migrated the Python environment with `python manage.py migrate`
- If you get hosed up, drop the db and recreate it  or the service with docker = docker compose --env-file .env.local up -d db
- Destroy the db eatart and recreate it
- Then `docker compose exec web python manage.py migrate`

## Files Involved

- `docker/postgres/restore-local.sh`
- `docker/postgres/migrate-piece-to-gallery.sh`
- `docker/postgres/migrate-piece-to-gallery.sql`
- `docker/postgres/verify-piece-to-gallery.sh`
- `docker/postgres/verify-piece-to-gallery.sql`

## Local Workflow

If already running:
Stop web and connections to the database:
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'eatart' AND pid <> pg_backend_pid();"
docker compose --env-file .env.local stop web


Local Table drop and restore (don't do this on Railway Production env)
# Drop and recreate the database inside the running container
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "DROP DATABASE eatart;"
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "CREATE DATABASE eatart;"

Start web
docker compose --env-file .env.local start web

# Apply Django migrations to rebuild the gallery_* schema
docker compose --env-file .env.local exec web python manage.py migrate

### 1. Start with the old dump

Restore the legacy database dump so the old `piece_*` tables exist:

```bash
./docker/postgres/restore-local.sh
```

This recreates the local database from the dump configured by `POSTGRES_DUMP_FILE`.

### 2. Create the new schema

Apply the current Django migrations so the new `gallery_*` tables exist alongside the old tables:

docker compose --env-file .env.local exec web python manage.py migrate


At this point both schemas should exist:

- legacy source tables: `piece_artist`, `piece_show`, `piece_event`, `piece_piece`, and related join tables
- new target tables: `gallery_artist`, `gallery_show`, `gallery_event`, `gallery_artwork`, and related join tables

### 3. Copy data into the new schema

Use pgAdmin to run the docker/postgres/migrate-piece-to-gallery.sql sql script

This script:

- truncates the `gallery_*` target tables
- copies rows from `piece_*` into `gallery_*`
- preserves primary keys
- copies many-to-many relationships
- resets sequences afterward

### 4. Verify the migration

Run the verification in pgAdmin

docker/postgres/verify-piece-to-gallery.sql

Expected outcome:

- source and target counts match for all core tables
- source and target counts match for all join tables
- all `missing_*` checks return `0`

### 5. Smoke test the app

After verification, run the gallery test suite and do a small manual check in the UI:

```bash
docker compose --env-file .env.local exec web python manage.py test gallery
docker compose --env-file .env.local exec web python manage.py runserver
```

Suggested manual checks:

- homepage renders
- shows list renders
- a show detail page renders with artworks and artists
- an artwork detail page renders
- an event detail page renders

## Pipeline Workflow

In every environment, make sure Django commands are pointed at PostgreSQL. In this repo, that means loading `.env.local` locally or setting the equivalent Railway variables in the deployment environment.

## Direct SQL Usage

If you need to run the SQL manually:

```bash
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f docker/postgres/migrate-piece-to-gallery.sql
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f docker/postgres/verify-piece-to-gallery.sql
```

If you need to run Django commands manually against Postgres locally:

```bash
set -a && source .env.local && set +a && python manage.py migrate
set -a && source .env.local && set +a && python manage.py test gallery
```

## Failure Handling

If verification reports a mismatch:

1. Do not delete the legacy `piece_*` tables.
2. Inspect the reported table or join-table mismatch.
3. Re-run the migration script after fixing the SQL or schema issue, since it truncates and reloads the `gallery_*` target tables.

Because the migration script is transactional, partial writes should not remain if the SQL fails before commit.

## Notes

- The SQL migration preserves IDs intentionally so existing references remain stable.
- The migration script expects the legacy source tables to still exist.
- The verification script checks both row counts and missing join relationships.
- The current gallery test suite includes regression coverage for the legacy public URLs served by the new structure.
- If `.env.local` is not loaded, Django falls back to SQLite in `eatart/settings.py`, which is the wrong target for this migration workflow.