from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0005_add_submission_deadline_to_show'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='is_public',
            field=models.BooleanField(default=False),
        ),
        migrations.RunSQL(
            sql=(
                'UPDATE gallery_artist '
                'SET is_public = TRUE '
                'WHERE id IN ('
                '    SELECT DISTINCT ga.id '
                '    FROM gallery_artist ga '
                '    INNER JOIN gallery_artwork_artists gaa ON gaa.artist_id = ga.id '
                '    INNER JOIN gallery_artwork gaw ON gaw.id = gaa.artwork_id '
                '    WHERE gaw.is_public = TRUE'
                ');'
            ),
            reverse_sql=(
                'UPDATE gallery_artist SET is_public = FALSE;'
            ),
        ),
    ]