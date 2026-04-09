# Railway And pgAdmin Migration Guide

## Purpose

This guide explains how to run the `piece_* -> gallery_*` data migration against a Railway PostgreSQL database and inspect or execute the SQL through a pgAdmin web interface.

Use this when:

- the target database lives on Railway
- you want to run Django migrations against Railway Postgres
- you want to execute or inspect the SQL migration in pgAdmin

## What You Need

- a Railway project with the application service and PostgreSQL service
- Railway database connection details
- access to a pgAdmin web instance that can connect to the Railway PostgreSQL service
- the files in this repo:
  - `docker/postgres/migrate-piece-to-gallery.sql`
  - `docker/postgres/verify-piece-to-gallery.sql`

## Important Constraint

The local shell helpers are Docker Compose wrappers for local development:

- `docker/postgres/restore-local.sh`
- `docker/postgres/migrate-piece-to-gallery.sh`
- `docker/postgres/verify-piece-to-gallery.sh`

Do not expect those wrapper scripts to run unchanged inside Railway.

On Railway, use:

- Django management commands against Railway Postgres
- direct SQL execution through pgAdmin Query Tool, `psql`, or Railway shell access

## Railway Variables You Need

From the Railway PostgreSQL service, collect:

- `PGHOST`
- `PGPORT`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`

If your Railway app already exposes `DATABASE_URL`, that can also be used for Django commands.

## Recommended High-Level Order

1. Restore the legacy database dump into the Railway PostgreSQL database or into a staging Railway database.
2. Run Django migrations against that Railway PostgreSQL database.
3. Run `migrate-piece-to-gallery.sql` against the same database.
4. Run `verify-piece-to-gallery.sql` against the same database.
5. Run `python manage.py test gallery` against the same PostgreSQL-backed environment if your operational flow supports that.
6. Smoke test key pages.

## Step 1: Connect pgAdmin To Railway Postgres

In pgAdmin:

1. Create a new server registration.
2. Under `General`, give it a descriptive name like `Railway Eatart`.
3. Under `Connection`, enter:
   - Host name/address: Railway Postgres host
   - Port: Railway Postgres port
   - Maintenance database: Railway database name
   - Username: Railway database user
   - Password: Railway database password
4. Save the server.

If Railway requires SSL for the exposed connection, configure SSL in pgAdmin accordingly.

## Step 2: Restore The Legacy Dump

You need the old `piece_*` tables in the Railway database first.

There are two common options.

### Option A: Restore with pgAdmin

If your dump format is supported by the pgAdmin Restore dialog:

1. Open the target Railway database in pgAdmin.
2. Use `Restore...` on the database.
3. Choose the dump file from `source/` or an uploaded copy of it.
4. Restore into the target database.

### Option B: Restore with pg_restore from a machine that can reach Railway

Use the Railway connection details and run something like:

```bash
PGPASSWORD="$PGPASSWORD" pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --schema=public \
  -h "$PGHOST" \
  -p "$PGPORT" \
  -U "$PGUSER" \
  -d "$PGDATABASE" \
  source/120710-database
```

After restore, confirm the legacy tables exist:

- `piece_artist`
- `piece_show`
- `piece_event`
- `piece_piece`
- `piece_piece_artists`
- `piece_piece_shows`
- `piece_show_curators`

You can check this in pgAdmin under the database tables list or with:

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'piece_%'
ORDER BY table_name;
```

## Step 3: Run Django Migrations Against Railway Postgres

This step creates the new `gallery_*` tables.

You can do this from a Railway shell, CI job, or a local shell that targets Railway Postgres.

If using `DATABASE_URL`:

```bash
DATABASE_URL="<railway database url>" python manage.py migrate
```

If using explicit Postgres variables:

```bash
export POSTGRES_HOST="$PGHOST"
export POSTGRES_PORT="$PGPORT"
export POSTGRES_DB="$PGDATABASE"
export POSTGRES_USER="$PGUSER"
export POSTGRES_PASSWORD="$PGPASSWORD"
python manage.py migrate
```

Important:

- do not run bare `python manage.py migrate` unless the environment is already pointed at Railway Postgres
- if the Postgres variables are missing, Django may use SQLite instead, which is not the target database

After this step, confirm these target tables exist:

- `gallery_artist`
- `gallery_show`
- `gallery_event`
- `gallery_artwork`
- `gallery_artwork_artists`
- `gallery_artwork_shows`
- `gallery_show_curators`

## Step 4: Run The Data Migration SQL In pgAdmin

In pgAdmin:

1. Open the Railway database.
2. Open `Query Tool`.
3. Paste the contents of `docker/postgres/migrate-piece-to-gallery.sql`.
4. Execute the query.

Expected result:

- the transaction commits successfully
- inserts occur for artists, shows, events, artworks, and join tables
- no missing-table or missing-column errors are raised

Notes:

- the script truncates `gallery_*` target tables before copying
- the script preserves IDs from the legacy `piece_*` rows
- the script already handles the older dump shape where `piece_artist` lacks `first_name` and `last_name`

## Step 5: Run The Verification SQL In pgAdmin

Still in pgAdmin Query Tool:

1. Open a new query tab or clear the previous one.
2. Paste the contents of `docker/postgres/verify-piece-to-gallery.sql`.
3. Execute the query.

Expected result:

- every count comparison row has `status = 'ok'`
- every `missing_*` row reports `0`

If any mismatch appears:

1. stop before promoting the database
2. inspect the mismatched table or join table
3. fix the migration SQL if needed
4. rerun the migration SQL, because it truncates and reloads the target tables

## Step 6: Run Application Validation Against Railway Postgres

Run Django tests against the Railway-backed environment if that is acceptable for your operational setup:

```bash
DATABASE_URL="<railway database url>" python manage.py test gallery
```

Or with explicit Postgres variables:

```bash
export POSTGRES_HOST="$PGHOST"
export POSTGRES_PORT="$PGPORT"
export POSTGRES_DB="$PGDATABASE"
export POSTGRES_USER="$PGUSER"
export POSTGRES_PASSWORD="$PGPASSWORD"
python manage.py test gallery
```

If you do not want to run tests against the live Railway database, run them against a Railway-connected staging database instead.

## Step 7: Manual Smoke Checks

After the SQL migration and verification succeed, manually check:

- homepage
- shows list
- one show detail page
- one artwork detail page
- one event detail page
- artist detail page

## Quick Query Checklist For pgAdmin

### Confirm legacy source tables exist

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'piece_%'
ORDER BY table_name;
```

### Confirm new target tables exist

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'gallery_%'
ORDER BY table_name;
```

### Confirm row counts after migration

```sql
SELECT COUNT(*) FROM piece_artist;
SELECT COUNT(*) FROM gallery_artist;
SELECT COUNT(*) FROM piece_piece;
SELECT COUNT(*) FROM gallery_artwork;
```

## Operational Advice

- Run this first in a staging Railway database if possible.
- Keep the original dump until the migration and smoke tests are complete.
- Treat pgAdmin as the SQL execution and inspection layer; use Django only for schema creation and application validation.
- Do not rely on the local Docker wrapper scripts inside Railway itself.