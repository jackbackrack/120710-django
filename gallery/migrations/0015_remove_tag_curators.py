from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0014_remove_artist_is_public_remove_artwork_is_public'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='tag',
            name='curators',
        ),
    ]
