#!/usr/bin/env bash
# Populate the database with test artists, artworks, shows, and jury data.
# Run from the project root: bash scripts/create_test_database.sh
set -e

DIR="$(dirname "$0")"

# Every show date is derived from the day the database is built, so a freshly seeded
# database always has a show you can actually submit to. Hard-coded dates silently rot:
# a show whose submission deadline has passed accepts nothing, which looks like a bug
# in the submission flow rather than stale fixtures.
eval "$(python - <<'PYDATES'
import datetime
today = datetime.date.today()
def d(n):
    return (today + datetime.timedelta(days=n)).isoformat()
vals = {
    # Past show: ran and closed last year.
    'PAST_YEAR':          str((today - datetime.timedelta(days=300)).year),
    'PAST_START':         d(-330), 'PAST_END': d(-240), 'PAST_DEADLINE': d(-360),
    # Invitation-only show, open for submissions right now.
    'INVITED_START':      d(60),  'INVITED_END': d(90),  'INVITED_DEADLINE': d(30),
    # Open call currently in jury review — deadline just passed.
    'REVIEW_START':       d(45),  'REVIEW_END': d(75),   'REVIEW_DEADLINE': d(-3),
    # Open call accepting submissions right now — the one to test the flow against.
    'OPEN_START':         d(90),  'OPEN_END': d(120),    'OPEN_DEADLINE': d(45),
    'OPEN_DECISION':      d(55),
}
for k, v in vals.items():
    print(f'{k}={v}')
PYDATES
)"
PAST_SHOW_NAME="Autumn Open $PAST_YEAR"
PAST_SHOW_SLUG="autumn-open-$PAST_YEAR"

ARTIST="python $DIR/create_test_artist.py"
ARTWORK="python $DIR/create_test_artwork.py"
SHOW="python $DIR/create_test_show.py"
SITE="python $DIR/create_test_site.py"

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

echo "=== Creating sites ==="

$SITE --name "120710" \
      --street "1207 10th Street" \
      --city "Berkeley" \
      --state "CA" \
      --postal-code "94710" \
      --country "USA" \
      --email "info@120710.art" \
      --instagram "@120710.art" \
      --website "https://www.120710.art" \
      --lat 37.881570 \
      --lng -122.297147 \
      --image test_fixtures/site_images/120710.jpg \
      --icon test_fixtures/site_icons/120710.png \
      --status published

echo "=== Creating room config and obstacles for 120710 ==="

python manage.py shell -c "
from gallery.models import Site
from gallery.models.room import RoomConfig, WallObstacle, SiteSupport

site = Site.objects.get(slug='120710')
cfg, _ = RoomConfig.objects.get_or_create(
    site=site,
    defaults={'width_in': 384.0, 'depth_in': 576.0, 'height_in': 120.0},
)
# Ensure correct dimensions even if it already existed
cfg.width_in  = 384.0   # 32 ft E-W
cfg.depth_in  = 576.0   # 48 ft N-S
cfg.height_in = 120.0   # 10 ft
cfg.save()

WallObstacle.objects.filter(room_config=cfg).delete()

# North wall: centered door 9' wide x 8' high (bottom at floor)
WallObstacle.objects.create(
    room_config=cfg, wall='N', label='Door',
    x_in=0, y_in=48, z_in=0,   # center: x=0, y=48\" (half of 96\")
    w_in=108, h_in=96,           # 9' x 8'
)

# West wall: two doors 40\" wide x 7' high, 44\" gap from each end
# When viewing the west wall the south side is on the left, north on the right.
# Left door (south side): center z_in = +288 - 44 - 20 = +224\"
WallObstacle.objects.create(
    room_config=cfg, wall='W', label='Door (S)',
    x_in=0, y_in=42, z_in=224,  # center: z=+224\" (south side), y=42\" (half of 84\")
    w_in=40, h_in=84,
)
# Right door (north side): center z_in = -288 + 44 + 20 = -224\"
WallObstacle.objects.create(
    room_config=cfg, wall='W', label='Door (N)',
    x_in=0, y_in=42, z_in=-224, # center: z=-224\" (north side)
    w_in=40, h_in=84,
)
print('Room config: 384\" x 576\" x 120\"')
print('North wall: 1 door (9\\'x8\\', centered)')
print('West wall:  2 doors (40\"x7\\', 44\" from each end)')

# Support catalog (reusable pedestal / shelf definitions). A support is a plain
# cuboid W x H x D (inches); it reads as a pedestal on the floor and a shelf on a
# vertical wall. An optional texture maps onto all six faces; blank = white with a
# black outline. Curators add copies from this catalog in a show's layout tool.
import os
from django.core.files import File
SiteSupport.objects.filter(room_config=cfg).delete()
supports = [
    # (label, W, H, D, textured?)  — W x H x D in inches
    # Pedestals: standing cuboids, tall H, square-ish footprint
    ('Pedestal - Small',  12, 36, 12, False),
    ('Pedestal - Medium', 16, 40, 16, True),   # wood texture, to show a textured support
    ('Pedestal - Large',  20, 44, 20, False),
    # Shelves: wide along the wall, thin H (thickness), shallow D (projection)
    ('Shelf - Narrow',    24,  2,  8, False),
    ('Shelf - Wide',      48,  2, 10, True),    # wood texture
]
wood = 'static/img/fine-wood-grain-3.png'
for label, w, h, d, textured in supports:
    ss = SiteSupport.objects.create(room_config=cfg, label=label, w_in=w, h_in=h, d_in=d)
    if textured and os.path.exists(wood):
        with open(wood, 'rb') as f:
            ss.texture.save(os.path.basename(wood), File(f), save=True)
n_tex = sum(1 for s in supports if s[4])
print(f'Support catalog: {len(supports)} entries (3 pedestals, 2 shelves; {n_tex} textured)')
"

echo "=== Creating artists ==="

$ARTIST --email oliver@hawk.com --password b8 --curator \
        --first Oliver --last Hawk --image test_fixtures/artist_images/oliver-hawk.jpg

$ARTIST --email jonathan@bachrach.com --password b8 --curator \
        --first Jonathan --last Bachrach --image test_fixtures/artist_images/jrb-400.png

$ARTIST --email miguel@novelo.com --password b8 --artist --zipcode 94710 \
        --first Miguel --last Novelo --image test_fixtures/artist_images/miguel-novelo.jpg

$ARTIST --email laura@rokas.com --password b8 --artist --zipcode 94710 \
        --first Laura --last Rokas --image test_fixtures/artist_images/laura-rokas.jpg

$ARTIST --email dave@carter.com --password b8 --artist --zipcode 94710 \
        --first Dave --last Carter --image test_fixtures/artist_images/dave-carter.jpg

# Accounts positioned at each step of the submission flow, so every state can be
# exercised without replaying signup. What blocks submission is a missing photo or
# zip code, so those are what differ between them.
$ARTIST --email ready@example.com --password b8 --artist \
        --first Sam --last Ready --zipcode 94710 \
        --image test_fixtures/artist_images/dave-carter.jpg

$ARTIST --email nophoto@example.com --password b8 --artist \
        --first Nadia --last Nophoto --zipcode 94710

$ARTIST --email newcomer@example.com --password b8 --artist \
        --first Ned --last Newcomer

$ARTIST --email invited@example.com --password b8 --artist \
        --first Ivy --last Invited --zipcode 94710 \
        --image test_fixtures/artist_images/laura-rokas.jpg

$ARTIST --email uninvited@example.com --password b8 --artist \
        --first Ursula --last Uninvited --zipcode 94710 \
        --image test_fixtures/artist_images/miguel-novelo.jpg

# Dedicated juror accounts for testing the jury workflow
$ARTIST --email juror1@example.com --password b8 --artist \
        --first Alice --last Juror

$ARTIST --email juror2@example.com --password b8 --artist \
        --first Bob --last Juror

echo "=== Creating past show ($PAST_SHOW_NAME, closed) ==="

$SHOW --name "$PAST_SHOW_NAME" \
      --start "$PAST_START" --end "$PAST_END" \
      --submission-deadline "$PAST_DEADLINE" \
      --status closed \
      --curator oliver@hawk.com \
      --site 120710 \
      --image test_fixtures/show_images/234tgrwith_logo_copy.jpg

echo "=== Creating artworks (submitted to Autumn Open 2025) ==="

$ARTWORK --email oliver@hawk.com --name "Oliver" \
         --year 2024 --width 12 --height 16 \
         --medium "Oil on canvas" \
         --show "$PAST_SHOW_SLUG" \
         --image test_fixtures/piece_images/Imaged_two_-_Oliver_Holden.jpg

$ARTWORK --email dave@carter.com --name "Drawing" \
         --year 2024 --width 12 --height 16 \
         --medium "Graphite on paper" \
         --show "$PAST_SHOW_SLUG" \
         --image test_fixtures/piece_images/IMG_2448_-_David_Carter.jpeg

$ARTWORK --email laura@rokas.com --name "Quilt" \
         --year 2025 --width 18 --height 24 \
         --medium "Textile" \
         --show "$PAST_SHOW_SLUG" \
         --image test_fixtures/piece_images/LR2201_Tinsignia_60_x_45-sm_-_Laura_Rokas_Berube.jpg

$ARTWORK --email miguel@novelo.com --name "Rock Worship" \
         --year 2025 --width 18 --height 24 \
         --medium "Mixed media" \
         --show "$PAST_SHOW_SLUG" \
         --image test_fixtures/piece_images/miguel-rock_small.jpg

echo "=== Promoting all artworks into $PAST_SHOW_NAME ==="

python manage.py shell -c "
from gallery.models import Show, ArtworkSubmission
show = Show.objects.get(slug='$PAST_SHOW_SLUG')
for sub in ArtworkSubmission.objects.filter(show=show):
    sub.curator_decision = ArtworkSubmission.CURATOR_SELECTED
    sub.save()
    show.artworks.add(sub.artwork)
    print(f'Promoted: {sub.artwork.name}')
"

echo "=== Creating active shows ==="

$SHOW --name "Working Craft" \
      --start "$INVITED_START" --end "$INVITED_END" \
      --submission-deadline "$INVITED_DEADLINE" \
      --curator oliver@hawk.com \
      --image test_fixtures/show_images/234tgrwith_logo_copy.jpg \
      --site 120710 \
      --status published \
      --invited

$SHOW --name "Feel-Full" \
      --start "$REVIEW_START" --end "$REVIEW_END" \
      --submission-deadline "$REVIEW_DEADLINE" \
      --image test_fixtures/show_images/far-away-is-now-updated.jpg \
      --curator jonathan@bachrach.com \
      --site 120710 \
      --status published

echo "=== Creating artworks ==="

$ARTWORK --email oliver@hawk.com --name "Oliver" \
         --year 2024 --width 12 --height 16 \
         --image test_fixtures/piece_images/Imaged_two_-_Oliver_Holden.jpg \
         --show working-craft

$ARTWORK --email dave@carter.com --name "Drawing" \
         --year 2024 --width 12 --height 16 \
         --image test_fixtures/piece_images/IMG_2448_-_David_Carter.jpeg \
         --show working-craft

$ARTWORK --email laura@rokas.com --name "Quilt" \
         --year 2025 --width 18 --height 24 \
         --image test_fixtures/piece_images/LR2201_Tinsignia_60_x_45-sm_-_Laura_Rokas_Berube.jpg \
         --show feel-full

$ARTWORK --email miguel@novelo.com --name "Rock Worship" \
         --year 2025 --width 18 --height 24 \
         --image test_fixtures/piece_images/miguel-rock_small.jpg \
         --show feel-full

echo "=== Submitting artworks to Feel-Full ==="

# Separate copies submitted to the open call — distinct from the Autumn Open artworks
$ARTWORK --email oliver@hawk.com --name "Oliver (Feel-Full)" \
         --year 2024 --width 12 --height 16 \
         --medium "Oil on canvas" \
         --show feel-full \
         --image test_fixtures/piece_images/Imaged_two_-_Oliver_Holden.jpg

$ARTWORK --email dave@carter.com --name "Drawing (Feel-Full)" \
         --year 2024 --width 12 --height 16 \
         --medium "Graphite on paper" \
         --show feel-full \
         --image test_fixtures/piece_images/IMG_2448_-_David_Carter.jpeg

$ARTWORK --email laura@rokas.com --name "Quilt (Feel-Full)" \
         --year 2025 --width 18 --height 24 \
         --medium "Textile" \
         --show feel-full \
         --image test_fixtures/piece_images/LR2201_Tinsignia_60_x_45-sm_-_Laura_Rokas_Berube.jpg

$ARTWORK --email miguel@novelo.com --name "Rock Worship (Feel-Full)" \
         --year 2025 --width 18 --height 24 \
         --medium "Mixed media" \
         --show feel-full \
         --image test_fixtures/piece_images/miguel-rock_small.jpg

echo "=== Seeding the varied catalogue from the real Feel-Full show ==="

# The four artworks above reuse four images, and the artists above reuse two photos, so
# every card grid showed the same handful of pieces tiling — which reads as a broken page
# rather than a gallery, and those grids appear in the published how-to screenshots.
# This adds 20 real pieces by 20 different artists, each kept with its actual maker.
# See scripts/create_catalogue.py.
# Twice on purpose. A piece is only *publicly* visible once it is in a published or
# closed show, so the first call is what makes the Artworks gallery look like a gallery
# rather than four lonely cards; the second gives the jury and curation guides a realistic
# number of submissions to score on a show that is still in review.
python "$DIR/create_catalogue.py" test_fixtures/full_feel_catalogue.json \
       --show "$PAST_SHOW_SLUG" --status accepted
python "$DIR/create_catalogue.py" test_fixtures/full_feel_catalogue.json \
       --show feel-full --status submitted

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

# Scores use the five button values: 10=poor, 30=below avg, 50=avg, 70=good, 90=excellent
# Jurors disagree on Rock Worship to show interesting curation tension
juror_scores = {
    juror1: [
        (70, 70),   # Oliver       — good across the board
        (50, 30),   # Drawing      — average originality, below-avg execution
        (90, 70),   # Quilt        — excellent originality, good execution
        (30, 50),   # Rock Worship — below-avg originality, average execution
    ],
    juror2: [
        (70, 90),   # Oliver       — good originality, excellent execution
        (50, 70),   # Drawing      — average originality, good execution
        (70, 90),   # Quilt        — good originality, excellent execution
        (90, 70),   # Rock Worship — juror2 rates this best: excellent originality
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

echo "=== Creating an open call that is accepting submissions now ==="

# Feel-Full ends up in jury review above, and Working Craft is invitation-only, so
# neither can be used to walk the open-call submission flow. This one stays open.
$SHOW --name "Open Studio" \
      --start "$OPEN_START" --end "$OPEN_END" \
      --submission-deadline "$OPEN_DEADLINE" \
      --decision-date "$OPEN_DECISION" \
      --curator oliver@hawk.com \
      --site 120710 \
      --status open_call \
      --image test_fixtures/show_images/far-away-is-now-updated.jpg

echo "=== Inviting artists to Working Craft (invitation only) ==="

python manage.py shell -c "
from django.contrib.auth import get_user_model
from gallery.models import Show, ShowInvitation
User = get_user_model()
show = Show.objects.get(slug='working-craft')
show.status = Show.STATUS_OPEN_CALL
show.save(update_fields=['status'])
for email in ['invited@example.com', 'ready@example.com']:
    inv, created = ShowInvitation.objects.get_or_create(show=show, email=email)
    user = User.objects.filter(email=email).first()
    artist = user.artists.first() if user else None
    if artist and inv.artist_id is None:
        inv.artist = artist
        inv.save(update_fields=['artist'])
    print(f'Invited {email} to Working Craft' + ('' if created else ' (already invited)'))
print('uninvited@example.com deliberately NOT invited — use it to test the block')
"

echo "=== Setting up collectors and pinned artworks ==="

python manage.py shell -c "
from django.contrib.auth import get_user_model
from gallery.models import Artwork
from gallery.models.collection import CollectionPiece, SavedArtwork

User = get_user_model()

oliver  = User.objects.get(email='oliver@hawk.com')
dave    = User.objects.get(email='dave@carter.com')
laura   = User.objects.get(email='laura@rokas.com')
miguel  = User.objects.get(email='miguel@novelo.com')
juror1  = User.objects.get(email='juror1@example.com')
juror2  = User.objects.get(email='juror2@example.com')

def artwork(name):
    return Artwork.objects.filter(name=name).first()

# Confirmed purchases (owners) — referencing the Autumn Open 2025 artworks by exact name
# oliver bought 3 works, dave bought 2, laura bought 1
purchases = [
    (oliver, 'Drawing',       '2025-03-10', 800),
    (oliver, 'Quilt',         '2025-04-22', 1200),
    (oliver, 'Rock Worship',  '2025-06-01', 950),
    (dave,   'Oliver',        '2025-02-14', 600),
    (dave,   'Quilt',         '2025-05-30', 1100),
    (laura,  'Rock Worship',  '2025-07-04', 700),
]
for collector, name, date, price in purchases:
    aw = artwork(name)
    if aw:
        CollectionPiece.objects.get_or_create(
            collector=collector, artwork=aw,
            defaults={'purchase_date': date, 'purchase_price': price,
                      'status': CollectionPiece.STATUS_CONFIRMED}
        )
        print(f'{collector.email} owns \"{aw.name}\"')

# Pinned / saved artworks — all from Autumn Open 2025, so publicly visible
# Use the actual show artists (in published show) as pinners, not jurors
# miguel pinned 3, oliver pinned 2, laura pinned 1
pins = [
    (miguel, 'Oliver'),
    (miguel, 'Quilt'),
    (miguel, 'Drawing'),
    (oliver, 'Drawing'),
    (oliver, 'Rock Worship'),
    (laura,  'Oliver'),
]
for user, name in pins:
    aw = artwork(name)
    if aw:
        SavedArtwork.objects.get_or_create(user=user, artwork=aw)
        print(f'{user.email} pinned \"{aw.name}\"')
"

echo "=== Done ==="
echo ""
echo "Test accounts (all password: b8):"
echo "  admin@example.com      — superuser"
echo "  oliver@hawk.com        — curator of $PAST_SHOW_NAME (closed) and Open Studio; owns 3 works"
echo "  jonathan@bachrach.com  — curator of Feel-Full (in_review, 4 submissions, all scored)"
echo "  juror1@example.com     — juror on Feel-Full"
echo "  juror2@example.com     — juror on Feel-Full"
echo "  dave@carter.com        — artist; owns 2 works"
echo "  laura@rokas.com        — artist; owns 1 work, pinned 1"
echo "  miguel@novelo.com      — artist; pinned 3 artworks"
echo ""
echo "Submission-flow accounts — log in, open a show, follow the button:"
echo "  ready@example.com      — complete profile      → 'Submit Artwork'"
echo "  nophoto@example.com    — no photo              → 'Finish your profile (1 to go)'"
echo "  newcomer@example.com   — no photo, no zip      → 'Finish your profile (2 to go)'"
echo "  invited@example.com    — complete + invited    → can submit to Working Craft"
echo "  uninvited@example.com  — complete, NOT invited → no CTA on Working Craft"
echo "  (or sign up fresh: the confirmation email prints to the runserver console)"
echo ""
echo "Shows, dated relative to today ($(date +%Y-%m-%d)):"
echo "  open-studio     open call, ACCEPTING now      (deadline $OPEN_DEADLINE)"
echo "  working-craft   invitation only, ACCEPTING    (deadline $INVITED_DEADLINE)"
echo "  feel-full       open call, in jury review     (deadline $REVIEW_DEADLINE, passed)"
echo "  $PAST_SHOW_SLUG   closed"
