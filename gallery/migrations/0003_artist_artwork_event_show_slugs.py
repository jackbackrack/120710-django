from django.db import migrations, models
from django.utils.text import slugify


def build_unique_slug(model, value, pk):
    field = model._meta.get_field('slug')
    max_length = field.max_length
    base_slug = slugify(value or '') or model._meta.model_name
    base_slug = base_slug[:max_length].strip('-') or model._meta.model_name

    candidate = base_slug
    suffix = 2
    queryset = model.objects.exclude(pk=pk)
    while queryset.filter(slug=candidate).exists():
        suffix_text = f'-{suffix}'
        trimmed_base = base_slug[: max_length - len(suffix_text)].strip('-') or model._meta.model_name
        candidate = f'{trimmed_base}{suffix_text}'
        suffix += 1

    return candidate


def populate_slugs(apps, schema_editor):
    for model_name in ('Artist', 'Artwork', 'Show', 'Event'):
        model = apps.get_model('gallery', model_name)
        for instance in model.objects.all().iterator():
            instance.slug = build_unique_slug(model, instance.name, instance.pk)
            instance.save(update_fields=['slug'])


def clear_slugs(apps, schema_editor):
    for model_name in ('Artist', 'Artwork', 'Show', 'Event'):
        model = apps.get_model('gallery', model_name)
        model.objects.update(slug='')


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0002_artwork_created_by_artwork_is_public_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, max_length=255),
        ),
        migrations.AddField(
            model_name='artwork',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, max_length=255),
        ),
        migrations.AddField(
            model_name='event',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, max_length=255),
        ),
        migrations.AddField(
            model_name='show',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, max_length=255),
        ),
        migrations.RunPython(populate_slugs, clear_slugs),
        migrations.AlterField(
            model_name='artist',
            name='slug',
            field=models.SlugField(blank=True, max_length=255, unique=True),
        ),
        migrations.AlterField(
            model_name='artwork',
            name='slug',
            field=models.SlugField(blank=True, max_length=255, unique=True),
        ),
        migrations.AlterField(
            model_name='event',
            name='slug',
            field=models.SlugField(blank=True, max_length=255, unique=True),
        ),
        migrations.AlterField(
            model_name='show',
            name='slug',
            field=models.SlugField(blank=True, max_length=255, unique=True),
        ),
    ]