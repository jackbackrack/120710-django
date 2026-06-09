#!/usr/bin/env bash
# Populate the database with test artists, artworks, and shows.
# Run from the project root: bash scripts/create_test_database.sh
set -e

DIR="$(dirname "$0")"

ARTIST="python $DIR/create_test_artist.py"
ARTWORK="python $DIR/create_test_artwork.py"
SHOW="python $DIR/create_test_show.py"

SUPERUSER_EMAIL="admin@example.com"
SUPERUSER_PASSWORD="pass"

echo "=== Creating database ==="
if [ -n "$POSTGRES_DB" ]; then
    dropdb --if-exists "$POSTGRES_DB"
    createdb "$POSTGRES_DB"
else
    rm -f db.sqlite3
fi
python manage.py migrate --run-syncdb

echo "=== Creating superuser ==="
DJANGO_SUPERUSER_PASSWORD="$SUPERUSER_PASSWORD" \
  python manage.py createsuperuser --no-input \
    --username "$SUPERUSER_EMAIL" --email "$SUPERUSER_EMAIL"
echo "Created superuser: $SUPERUSER_EMAIL / $SUPERUSER_PASSWORD"

echo "=== Creating artists ==="

$ARTIST --email oliver@hawk.com --password pass --curator \
        --first Oliver --last Hawk --image media/artist_images/oliver-hawk.jpg

$ARTIST --email jonathan@bachrach.com --password pass --curator \
        --first Jonathan --last Bachrach --image media/artist_images/jrb-400.png

$ARTIST --email miguel@novelo.com --password pass --artist \
        --first Miguel --last Novelo --image media/artist_images/miguel-novelo.jpg

$ARTIST --email laura@rokas.com --password pass --artist \
        --first Laura --last Rokas --image media/artist_images/laura-rokas.jpg

$ARTIST --email dave@carter.com --password pass --artist \
        --first Dave --last Carter --image media/artist_images/dave-carter.jpg

echo "=== Creating artworks ==="

$ARTWORK --email oliver@hawk.com --name "Oliver" \
         --year 2024 --width 12 --height 16 \
         --image media/piece_images/Imaged_two_-_Oliver_Holden.jpg

$ARTWORK --email dave@carter.com --name "Drawing" \
         --year 2024 --width 12 --height 16 \
         --image media/piece_images/IMG_2448_-_David_Carter.jpeg

$ARTWORK --email laura@rokas.com --name "Quilt" \
         --year 2025 --width 18 --height 24 \
         --image media/piece_images/LR2201_Tinsignia_60_x_45-sm_-_Laura_Rokas_Berube.jpg

$ARTWORK --email miguel@novelo.com --name "Rock Worship" \
         --year 2025 --width 18 --height 24 \
         --image media/piece_images/miguel-rock_small.jpg

echo "=== Creating shows ==="

$SHOW --name "Working Craft" \
      --start 2026-07-01 --end 2026-07-25 \
      --submission-deadline 2026-06-15 \
      --curator oliver@hawk.com \
      --invited

$SHOW --name "Feel-Full" \
      --start 2026-08-01 --end 2026-08-25 \
      --submission-deadline 2026-07-15 \
      --curator jonathan@bachrach.com

echo "=== Done ==="
