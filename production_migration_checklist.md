# Production Migration Checklist (Legacy App -> Gallery + Reviews)

Use this one-page checklist during a maintenance window.

## Scope

- Migrates legacy `piece_*` content data into `gallery_*`.
- Requires `reviews_*` tables to exist before data copy.
- Legacy schema has no juror/review source data; juror assignment and initial reviews happen after cutover.

## Preflight (stop if any item is missing)

- [ ] New code (including `gallery` and `reviews`) is deployed.
- [ ] Railway Postgres credentials are available (`DATABASE_URL` or `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD`).
- [ ] `docker/postgres/migrate-piece-to-gallery.sql` and `docker/postgres/verify-piece-to-gallery.sql` are available.
- [ ] Maintenance window is active (no user writes expected).

## Copy/Paste Run Order (Shell)

Run in an environment that targets production Postgres.

```bash
set -euo pipefail

# 1) Point Django at production Postgres (choose one approach)
# export DATABASE_URL="<railway-database-url>"

# OR explicit vars:
# export POSTGRES_HOST="<pg-host>"
# export POSTGRES_PORT="<pg-port>"
# export POSTGRES_DB="<pg-db>"
# export POSTGRES_USER="<pg-user>"
# export POSTGRES_PASSWORD="<pg-password>"

# 2) Create/upgrade schema (gallery + reviews)
python manage.py migrate

# 3) Optional quick app-level validation before data copy
python manage.py test gallery reviews
```

## Copy/Paste Run Order (pgAdmin Query Tool)

### Query A: Pre-check source + target table presence

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'piece_artist', 'piece_show', 'piece_event', 'piece_piece',
    'piece_piece_artists', 'piece_piece_shows', 'piece_show_curators',
    'gallery_artist', 'gallery_show', 'gallery_event', 'gallery_artwork',
    'gallery_artwork_artists', 'gallery_artwork_shows', 'gallery_show_curators', 'gallery_show_artists',
    'reviews_showjuror', 'reviews_artworkreview'
  )
ORDER BY table_name;
```

### Query B: Run migration SQL

```sql
-- Paste and execute file: docker/postgres/migrate-piece-to-gallery.sql
```

### Query C: Run verification SQL

```sql
-- Paste and execute file: docker/postgres/verify-piece-to-gallery.sql
```

## Pass Criteria

- [ ] All `status` values in verification output are `ok`.
- [ ] All `missing_*` counts are `0`.
- [ ] `reviews_showjuror table exists` is `ok`.
- [ ] `reviews_artworkreview table exists` is `ok`.

## Post-Cutover (Reviews/Jurors)

- [ ] Assign at least one juror to one show in the reviews dashboard.
- [ ] Confirm assigned user has `juror` role.
- [ ] Submit one juror review (1..5 rating) for a show artwork.
- [ ] Confirm curator/staff view shows aggregate rating/review count.

## Smoke Check

- [ ] Homepage loads.
- [ ] Show detail loads with artworks.
- [ ] Artwork detail loads.
- [ ] Event detail loads.
- [ ] Show review dashboard loads.
- [ ] Juror assignment page loads.

## Rollback/Stop Rules

- Stop if Django migration fails.
- Stop if SQL migration throws any exception.
- Stop if verification returns any mismatch.
- Keep legacy `piece_*` tables intact for investigation/re-run.
