from django.db import migrations
from django.utils.text import slugify


def normalize_slug_value(value, fallback):
    slug = slugify(value or '') or fallback
    slug = slug.replace('_', '-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    return slug.strip('-') or fallback


def build_unique_slug(model, value, pk):
    field = model._meta.get_field('slug')
    max_length = field.max_length
    base_slug = normalize_slug_value(value, model._meta.model_name)
    base_slug = base_slug[:max_length].strip('-') or model._meta.model_name

    queryset = model.objects.exclude(pk=pk)
    candidate = base_slug
    suffix = 2

    while queryset.filter(slug=candidate).exists():
        suffix_text = f'-{suffix}'
        trimmed_base = base_slug[: max_length - len(suffix_text)].strip('-') or model._meta.model_name
        candidate = f'{trimmed_base}{suffix_text}'
        suffix += 1

    return candidate


def normalize_public_slugs(apps, schema_editor):
    for model_name in ('Artist', 'Artwork', 'Show', 'Event'):
        model = apps.get_model('gallery', model_name)
        for instance in model.objects.all().iterator():
            expected_slug = build_unique_slug(model, instance.name, instance.pk)
            if instance.slug != expected_slug:
                instance.slug = expected_slug
                instance.save(update_fields=['slug'])


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0003_artist_artwork_event_show_slugs'),
    ]

    operations = [
        migrations.RunPython(normalize_public_slugs, noop_reverse),
    ]