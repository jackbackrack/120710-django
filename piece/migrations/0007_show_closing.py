# Generated by Django 4.2.4 on 2023-08-21 01:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('piece', '0006_alter_piece_price'),
    ]

    operations = [
        migrations.AddField(
            model_name='show',
            name='closing',
            field=models.DateField(blank=True, null=True),
        ),
    ]
