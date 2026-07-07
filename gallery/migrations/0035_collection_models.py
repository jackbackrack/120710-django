from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_collection_to_pinned(apps, schema_editor):
    Artist = apps.get_model('gallery', 'Artist')
    SavedArtwork = apps.get_model('gallery', 'SavedArtwork')
    for artist in Artist.objects.filter(collection__isnull=False).prefetch_related('collection').distinct():
        if not artist.user_id:
            continue
        for artwork in artist.collection.all():
            SavedArtwork.objects.get_or_create(
                user_id=artist.user_id,
                artwork=artwork,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0034_artist_collection'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SavedArtwork',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('artwork', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_by', to='gallery.artwork')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_artworks', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('user', 'artwork')},
            },
        ),
        migrations.CreateModel(
            name='CollectionPiece',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('purchase_date', models.DateField(blank=True, null=True)),
                ('purchase_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('notes', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('pending', 'Pending confirmation'), ('confirmed', 'Confirmed'), ('declined', 'Declined')], default='pending', max_length=20)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('display_order', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('artwork', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='collection_pieces', to='gallery.artwork')),
                ('collector', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='collection_pieces', to=settings.AUTH_USER_MODEL)),
                ('confirmed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='confirmed_sales', to='gallery.artist')),
            ],
            options={
                'ordering': ['display_order', '-created_at'],
                'unique_together': {('collector', 'artwork')},
            },
        ),
        migrations.RunPython(migrate_collection_to_pinned, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='artist',
            name='collection',
        ),
    ]
