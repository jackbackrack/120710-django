#!/usr/bin/env bash
# Populate the database with test artists, artworks, and shows.
# Run from the project root: bash scripts/create_test_database.sh
set -e

DIR="$(dirname "$0")"

ARTIST="python $DIR/create_test_artist.py"
ARTWORK="python $DIR/create_test_artwork.py"
SHOW="python $DIR/create_test_show.py"

SUPERUSER_EMAIL="admin@example.com"
SUPERUSER_PASSWORD="adminpass123"

echo "=== Creating superuser ==="
DJANGO_SUPERUSER_PASSWORD="$SUPERUSER_PASSWORD" \
  python manage.py createsuperuser --no-input \
    --username "$SUPERUSER_EMAIL" --email "$SUPERUSER_EMAIL"
echo "Created superuser: $SUPERUSER_EMAIL / $SUPERUSER_PASSWORD"

echo "=== Creating artists ==="

$ARTIST --email curator@example.com --password testpass123 --curator \
        --first Alice --last Curator

$ARTIST --email artist1@example.com --password testpass123 --artist \
        --first Bob --last Artist

$ARTIST --email artist2@example.com --password testpass123 --artist \
        --first Carol --last Maker

echo "=== Creating artworks ==="

$ARTWORK --email artist1@example.com --name "Untitled 1" \
         --year 2024 --width 12 --height 16 \
         --image media/piece_images/minimal-camel.jpg

$ARTWORK --email artist2@example.com --name "Untitled 2" \
         --year 2025 --width 18 --height 24 \
         --image media/piece_images/out-of-body-lady-staring-small.jpg

echo "=== Creating shows ==="

$SHOW --name "Test Show" \
      --start 2026-01-01 --end 2026-02-01 \
      --curator curator@example.com

echo "=== Done ==="
