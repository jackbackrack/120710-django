# Generated by Django 4.2.4 on 2024-02-01 00:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('piece', '0011_rename_name_piece_title_artist_bio'),
    ]

    operations = [
        migrations.AddField(
            model_name='piece',
            name='replacement_cost',
            field=models.FloatField(blank=True, null=True),
        ),
    ]