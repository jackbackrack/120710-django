import re
from decimal import Decimal, InvalidOperation

from django.db import migrations


def parse_dimensions(s):
    if not s:
        return None, None, None
    parts = re.split(r'\s*[xX×]\s*', s.strip())
    nums = []
    for part in parts:
        m = re.search(r'[\d.]+', part)
        if m:
            try:
                nums.append(Decimal(m.group()))
            except InvalidOperation:
                pass
    return (
        nums[0] if len(nums) > 0 else None,
        nums[1] if len(nums) > 1 else None,
        nums[2] if len(nums) > 2 else None,
    )


def populate_dimension_fields(apps, schema_editor):
    Artwork = apps.get_model('gallery', 'Artwork')
    to_update = []
    for artwork in Artwork.objects.exclude(dimensions='').exclude(dimensions__isnull=True):
        w, h, d = parse_dimensions(artwork.dimensions)
        if w is not None or h is not None:
            artwork.width_inches = w
            artwork.height_inches = h
            artwork.depth_inches = d
            to_update.append(artwork)
    if to_update:
        Artwork.objects.bulk_update(to_update, ['width_inches', 'height_inches', 'depth_inches'])


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0010_artwork_dimension_fields'),
    ]

    operations = [
        migrations.RunPython(populate_dimension_fields, migrations.RunPython.noop),
    ]
