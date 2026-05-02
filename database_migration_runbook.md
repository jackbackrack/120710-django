# Database Migration Runbook

## Purpose

This runbook documents the safest path to migrate data from the legacy `piece_*` schema into the new `gallery_*` schema without data loss, while also ensuring the `reviews` app schema is present for jurors and ratings.

For maintenance-window execution, use the one-page checklist: `production_migration_checklist.md`.

It assumes:

- the old database dump contains the legacy `piece_*` tables
- the current codebase uses the new `gallery` and `reviews` apps and migrations
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
docker compose --env-file .env.local stop web
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'eatart' AND pid <> pg_backend_pid();"


Local Table drop and restore (don't do this on Railway Production env)
# Drop and recreate the database inside the running container
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "DROP DATABASE eatart;"
docker compose --env-file .env.local exec db psql -U eatart -d postgres -c "CREATE DATABASE eatart;"

Start web
docker compose --env-file .env.local start web

# Apply Django migrations to rebuild the gallery_* and reviews_* schema
docker compose --env-file .env.local exec web python manage.py migrate

### 1. Start with the old dump

Restore the legacy database dump so the old `piece_*` tables exist:

```bash
./docker/postgres/restore-local.sh
```

This recreates the local database from the dump configured by `POSTGRES_DUMP_FILE`.

### 2. Create the new schema

Apply the current Django migrations so the new `gallery_*` and `reviews_*` tables exist alongside the old tables:

docker compose --env-file .env.local exec web python manage.py migrate


At this point both schemas should exist:

- legacy source tables: `piece_artist`, `piece_show`, `piece_event`, `piece_piece`, and related join tables
- new target tables: `gallery_artist`, `gallery_show`, `gallery_event`, `gallery_artwork`, and related join tables
- review target tables: `reviews_showjuror`, `reviews_artworkreview`

### 3. Copy data into the new schema

Use pgAdmin to run the docker/postgres/migrate-piece-to-gallery.sql sql script

This script:

- truncates the `gallery_*` target tables
- copies rows from `piece_*` into `gallery_*`
- preserves primary keys
- copies many-to-many relationships
- resets sequences afterward
- verifies that `reviews_showjuror` and `reviews_artworkreview` tables exist (schema readiness gate)

### 4. Verify the migration

Run the verification in pgAdmin

docker/postgres/verify-piece-to-gallery.sql

Expected outcome:

- source and target counts match for all core tables
- source and target counts match for all join tables
- all `missing_*` checks return `0`
- review-table existence checks report `ok`

### 5. Smoke test the app

After verification, run the gallery/reviews test suites and do a small manual check in the UI:

```bash
docker compose --env-file .env.local exec web python manage.py test gallery reviews
docker compose --env-file .env.local exec web python manage.py runserver
```

Suggested manual checks:

- homepage renders
- shows list renders
- a show detail page renders with artworks and artists
- an artwork detail page renders
- an event detail page renders
- show review dashboard loads for curator/staff
- juror assignment page loads for a show

### 6. Production cutover steps for reviews/jurors

The legacy `piece_*` schema has no reviews/juror equivalent. After code deploy + migrate + data copy:

- assign jurors per show in the review dashboard flow
- verify juror users have the `juror` group after assignment
- verify jurors can submit one 1..5 rating per artwork per show

No legacy review data is migrated because there is no source table in `piece_*`.

## Pipeline Workflow

In every environment, make sure Django commands are pointed at PostgreSQL. In this repo, that means loading `.env.local` locally or setting the equivalent Railway variables in the deployment environment.

### Production cutover (legacy app still live)

If production is still running the old app, use this order:

1. Deploy the new code version (gallery + reviews) first.
2. Run `python manage.py migrate` against production Postgres.
3. Run `docker/postgres/migrate-piece-to-gallery.sql` in pgAdmin.
4. Run `docker/postgres/verify-piece-to-gallery.sql` and confirm all checks pass.
5. Run post-cutover juror assignment + rating smoke checks.

Do this in a maintenance window to avoid writes during migration.

## Direct SQL Usage

If you need to run the SQL manually:

```bash
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f docker/postgres/migrate-piece-to-gallery.sql
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f docker/postgres/verify-piece-to-gallery.sql
```

If you need to run Django commands manually against Postgres locally:

```bash
set -a && source .env.local && set +a && python manage.py migrate
set -a && source .env.local && set +a && python manage.py test gallery reviews
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
- Reviews tables are schema prerequisites only in this migration; juror/review rows are created post-cutover in the new app.
- The current gallery test suite includes regression coverage for the legacy public URLs served by the new structure.
- If `.env.local` is not loaded, Django falls back to SQLite in `eatart/settings.py`, which is the wrong target for this migration workflow.