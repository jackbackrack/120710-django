# Generated by Django 4.2.4 on 2024-02-01 00:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('piece', '0010_alter_piece_end_year'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='bio',
            field=models.TextField(blank=True, null=True),
        ),
    ]
