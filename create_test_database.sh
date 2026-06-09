#!/usr/bin/env bash
# Populate the database with test artists, artworks, and shows.
set -e

MANAGE="python manage.py"
ARTIST="python create_test_artist.py"
ARTWORK="python create_test_artwork.py"
SHOW="python create_test_show.py"

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
