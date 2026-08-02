"""How long `manage.py generateimages` will take, measured rather than guessed.

    ./env/bin/python manage.py shell < scripts/estimate_image_regen.py

Counts what actually has to be generated, then times a real sample of it — source fetch
included, which on S3 is most of the cost. Read-only apart from the sample it generates,
and those are files generateimages would write anyway.

Why the count is not just "images x 22": a spec only produces a file where its *source*
field is populated. An artwork with no layout_image owes nothing to layout_lg or
layout_sm, and most do not have one.
"""
import time

from imagekit.registry import generator_registry

from gallery.models import Artist, Artwork, Show, Site
from gallery.models.artworks import ArtworkImage

# Which source field feeds which specs, from the registry: gallery:<model>:<spec>.
SOURCES = [
    ('artwork', Artwork, 'image', ['card_sm', 'card_md', 'detail_lg', 'slideshow']),
    ('artwork', Artwork, 'layout_image', ['layout_lg', 'layout_sm']),
    ('artworkimage', ArtworkImage, 'image', ['card_sm', 'card_md', 'slideshow']),
    ('artist', Artist, 'image', ['card_sm', 'card_md', 'detail_lg', 'slideshow']),
    ('show', Show, 'image', ['card_sm', 'card_md', 'detail_lg', 'slideshow']),
    ('site', Site, 'image', ['card_sm', 'card_md', 'detail_lg']),
    ('site', Site, 'icon', ['icon_sm', 'icon_md']),
]

print('what has to be generated')
print('-' * 58)
total = 0
for label, model, field, specs in SOURCES:
    rows = model.objects.exclude(**{field: ''}).exclude(**{f'{field}__isnull': True}).count()
    derived = rows * len(specs)
    total += derived
    print(f'  {label + "." + field:26} {rows:6} x {len(specs)} specs = {derived:7}')
print(f'  {"TOTAL DERIVATIVES":26} {total:>24}')

registered = len([i for i in generator_registry._generators if i.startswith('gallery:')])
print(f'\n  ({registered} specs registered; a spec with no populated source costs nothing)')

# ── Time a real sample, source fetch and all ────────────────────────────────
PER_SOURCE = 3          # rows sampled per source field; 7 fields, so up to ~21 samples
print('\ntiming a sample of real derivatives (fetch + convert + upload)')
print('-' * 58)
sampled, elapsed = 0, 0.0
for _label, model, field, specs in SOURCES:
    rows = (model.objects.exclude(**{field: ''})
            .exclude(**{f'{field}__isnull': True}).order_by('-pk')[:PER_SOURCE])
    for row in rows:
        for spec in specs[:1]:                       # one spec each, to spread the sample
            started = time.perf_counter()
            try:
                getattr(row, spec).generate(force=True)
            except Exception as exc:
                print(f'  skipped {model.__name__} {row.pk} {spec}: {exc}')
                continue
            took = time.perf_counter() - started
            elapsed += took
            sampled += 1
            print(f'  {model.__name__:14} {spec:10} {took:6.2f}s')

if sampled:
    per = elapsed / sampled
    print(f'\n  average {per:.2f}s per derivative over {sampled} samples')
    seconds = per * total
    print(f'  estimate for all {total}: {seconds/60:.0f} min ({seconds/3600:.1f} h), '
          f'single-threaded')
    print('\n  Caveat: generateimages loops spec-by-spec, so it re-fetches each source '
          'once\n  per spec rather than once per image. The sample above does the same, '
          'so this\n  already accounts for it.')
else:
    print('  nothing to sample — no images on this database')
