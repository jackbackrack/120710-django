#!/usr/bin/env bash
# Populate the database with test artists, artworks, shows, and jury data.
# Run from the project root: bash scripts/create_test_database.sh
set -e

DIR="$(dirname "$0")"

ARTIST="python $DIR/create_test_artist.py"
ARTWORK="python $DIR/create_test_artwork.py"
SHOW="python $DIR/create_test_show.py"

SUPERUSER_EMAIL="admin@example.com"
SUPERUSER_PASSWORD="b8"

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
python manage.py shell -c "
from django.contrib.auth import get_user_model
from allauth.account.models import EmailAddress
User = get_user_model()
user = User.objects.get(email='$SUPERUSER_EMAIL')
EmailAddress.objects.get_or_create(user=user, email='$SUPERUSER_EMAIL', defaults={'primary': True, 'verified': True})
"
echo "Created superuser: $SUPERUSER_EMAIL / $SUPERUSER_PASSWORD"

echo "=== Creating artists ==="

$ARTIST --email oliver@hawk.com --password b8 --curator \
        --first Oliver --last Hawk --image media/artist_images/oliver-hawk.jpg

$ARTIST --email jonathan@bachrach.com --password b8 --curator \
        --first Jonathan --last Bachrach --image media/artist_images/jrb-400.png

$ARTIST --email miguel@novelo.com --password b8 --artist \
        --first Miguel --last Novelo --image media/artist_images/miguel-novelo.jpg

$ARTIST --email laura@rokas.com --password b8 --artist \
        --first Laura --last Rokas --image media/artist_images/laura-rokas.jpg

$ARTIST --email dave@carter.com --password b8 --artist \
        --first Dave --last Carter --image media/artist_images/dave-carter.jpg

# Dedicated juror accounts for testing the jury workflow
$ARTIST --email juror1@example.com --password b8 --artist \
        --first Alice --last Juror

$ARTIST --email juror2@example.com --password b8 --artist \
        --first Bob --last Juror

echo "=== Creating artworks ==="

$ARTWORK --email oliver@hawk.com --name "Oliver" \
         --year 2024 --width 12 --height 16 \
         --medium "Oil on canvas" \
         --image media/piece_images/Imaged_two_-_Oliver_Holden.jpg

$ARTWORK --email dave@carter.com --name "Drawing" \
         --year 2024 --width 12 --height 16 \
         --medium "Graphite on paper" \
         --image media/piece_images/IMG_2448_-_David_Carter.jpeg

$ARTWORK --email laura@rokas.com --name "Quilt" \
         --year 2025 --width 18 --height 24 \
         --medium "Textile" \
         --image media/piece_images/LR2201_Tinsignia_60_x_45-sm_-_Laura_Rokas_Berube.jpg

$ARTWORK --email miguel@novelo.com --name "Rock Worship" \
         --year 2025 --width 18 --height 24 \
         --medium "Mixed media" \
         --image media/piece_images/miguel-rock_small.jpg

echo "=== Creating shows ==="

$SHOW --name "Working Craft" \
      --start 2026-07-01 --end 2026-07-25 \
      --submission-deadline 2026-06-15 \
      --curator oliver@hawk.com \
      --image media/show_images/234tgrwith_logo_copy.jpg \
      --invited

$SHOW --name "Feel-Full" \
      --start 2026-08-01 --end 2026-08-25 \
      --submission-deadline 2026-07-15 \
      --image media/show_images/far-away-is-now-updated.jpg \
      --curator jonathan@bachrach.com

echo "=== Submitting artworks to Feel-Full ==="

# Re-create artworks with --show flag to submit them
$ARTWORK --email oliver@hawk.com --name "Oliver (Feel-Full)" \
         --year 2024 --width 12 --height 16 \
         --medium "Oil on canvas" \
         --show feel-full \
         --image media/piece_images/Imaged_two_-_Oliver_Holden.jpg

$ARTWORK --email dave@carter.com --name "Drawing (Feel-Full)" \
         --year 2024 --width 12 --height 16 \
         --medium "Graphite on paper" \
         --show feel-full \
         --image media/piece_images/IMG_2448_-_David_Carter.jpeg

$ARTWORK --email laura@rokas.com --name "Quilt (Feel-Full)" \
         --year 2025 --width 18 --height 24 \
         --medium "Textile" \
         --show feel-full \
         --image media/piece_images/LR2201_Tinsignia_60_x_45-sm_-_Laura_Rokas_Berube.jpg

$ARTWORK --email miguel@novelo.com --name "Rock Worship (Feel-Full)" \
         --year 2025 --width 18 --height 24 \
         --medium "Mixed media" \
         --show feel-full \
         --image media/piece_images/miguel-rock_small.jpg

echo "=== Setting up jury for Feel-Full ==="

python manage.py shell -c "
from django.contrib.auth import get_user_model
from gallery.models import Show, ArtworkSubmission
from reviews.models import ShowJuror, RubricCriterion, ArtworkReview, CriterionScore

User = get_user_model()

show = Show.objects.get(slug='feel-full')
juror1 = User.objects.get(email='juror1@example.com')
juror2 = User.objects.get(email='juror2@example.com')
curator_user = User.objects.get(email='jonathan@bachrach.com')

# Assign both jurors
ShowJuror.objects.get_or_create(show=show, user=juror1, defaults={'assigned_by': curator_user})
ShowJuror.objects.get_or_create(show=show, user=juror2, defaults={'assigned_by': curator_user})
print('Assigned juror1@example.com and juror2@example.com as jurors on Feel-Full')

# Create rubric with two criteria
orig, _ = RubricCriterion.objects.get_or_create(
    show=show, name='Originality', defaults={'percentage': 60.0, 'order': 0}
)
exec_, _ = RubricCriterion.objects.get_or_create(
    show=show, name='Technical Execution', defaults={'percentage': 40.0, 'order': 1}
)
print('Created rubric: Originality (60%) + Technical Execution (40%)')

# Scores: juror1 and juror2 each review all four submissions with varied scores
# so the curation dashboard shows realistic weighted averages and ranking
juror_scores = {
    juror1: [
        (85, 78),   # Oliver    — strong
        (72, 65),   # Drawing   — good
        (90, 88),   # Quilt     — best
        (60, 55),   # Rock Worship — weakest for this juror
    ],
    juror2: [
        (78, 82),   # Oliver
        (68, 70),   # Drawing
        (80, 75),   # Quilt
        (88, 92),   # Rock Worship — juror2 rates this highest
    ],
}

submissions = list(ArtworkSubmission.objects.filter(show=show).order_by('submitted_at'))
for juror, scores in juror_scores.items():
    for sub, (o_score, e_score) in zip(submissions, scores):
        review, _ = ArtworkReview.objects.get_or_create(
            show=show, artwork=sub.artwork, juror=juror,
            defaults={'rating': None, 'body': ''}
        )
        CriterionScore.objects.get_or_create(review=review, criterion=orig, defaults={'score': o_score})
        CriterionScore.objects.get_or_create(review=review, criterion=exec_, defaults={'score': e_score})
    print(f'All 4 artworks scored by {juror.email}')

# Advance show to In Review so jury scoring is immediately active
show.status = Show.STATUS_IN_REVIEW
show.save(update_fields=['status'])
print('Set Feel-Full status to In Review')
print()
print('Weighted scores (Originality 60% + Execution 40%):')
for sub in submissions:
    reviews = ArtworkReview.objects.filter(show=show, artwork=sub.artwork).prefetch_related('criterion_scores')
    totals = []
    for r in reviews:
        scores_map = {cs.criterion_id: cs.score for cs in r.criterion_scores.all()}
        w = scores_map.get(orig.pk, 0) * 0.6 + scores_map.get(exec_.pk, 0) * 0.4
        totals.append(w)
    avg = sum(totals) / len(totals) if totals else 0
    print(f'  {sub.artwork.name}: {avg:.1f}')
"

echo "=== Done ==="
echo ""
echo "Test accounts (all password: b8):"
echo "  admin@example.com      — superuser"
echo "  jonathan@bachrach.com  — curator of Feel-Full (in_review, 4 submissions, all scored)"
echo "  juror1@example.com     — juror on Feel-Full (Alice Juror, all artworks scored)"
echo "  juror2@example.com     — juror on Feel-Full (Bob Juror, all artworks scored)"
echo "  oliver@hawk.com        — submitting artist (Oliver Feel-Full)"
echo "  dave@carter.com        — submitting artist (Drawing Feel-Full)"
echo "  laura@rokas.com        — submitting artist (Quilt Feel-Full)"
echo "  miguel@novelo.com      — submitting artist (Rock Worship Feel-Full)"
