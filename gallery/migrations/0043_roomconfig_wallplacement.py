import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0042_alter_image_upload'),
    ]

    operations = [
        migrations.CreateModel(
            name='RoomConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('width_in', models.FloatField(default=384.0)),
                ('depth_in', models.FloatField(default=576.0)),
                ('height_in', models.FloatField(default=120.0)),
                ('site', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='room_config', to='gallery.site')),
            ],
        ),
        migrations.CreateModel(
            name='WallPlacement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('wall', models.CharField(choices=[('N', 'North'), ('E', 'East'), ('S', 'South'), ('W', 'West'), ('ceiling', 'Ceiling'), ('floor', 'Floor')], max_length=8)),
                ('x_in', models.FloatField()),
                ('y_in', models.FloatField()),
                ('z_in', models.FloatField()),
                ('artwork', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wall_placements', to='gallery.artwork')),
                ('show', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wall_placements', to='gallery.show')),
            ],
        ),
        migrations.AlterUniqueTogether(
            name='wallplacement',
            unique_together={('show', 'artwork')},
        ),
    ]
