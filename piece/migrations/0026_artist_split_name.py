from django.db import migrations, models


def split_artist_name(apps, schema_editor):
    Artist = apps.get_model('piece', 'Artist')

    for artist in Artist.objects.all():
        full_name = (artist.name or '').strip()
        if not full_name:
            continue

        parts = full_name.split(None, 1)
        artist.first_name = parts[0]
        artist.last_name = parts[1] if len(parts) > 1 else ''
        artist.save(update_fields=['first_name', 'last_name'])


class Migration(migrations.Migration):

    dependencies = [
        ('piece', '0025_alter_event_options_alter_show_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='first_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='artist',
            name='last_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(split_artist_name, migrations.RunPython.noop),
    ]