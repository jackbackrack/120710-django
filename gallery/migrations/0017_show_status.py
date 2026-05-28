from django.db import migrations, models


def set_existing_shows_closed(apps, schema_editor):
    Show = apps.get_model('gallery', 'Show')
    Show.objects.all().update(status='closed')


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0016_set_null_on_user_fks'),
    ]

    operations = [
        migrations.AddField(
            model_name='show',
            name='status',
            field=models.CharField(
                choices=[
                    ('under_consideration', 'Under Consideration'),
                    ('open_call', 'Open Call'),
                    ('in_review', 'In Review'),
                    ('draft', 'Draft'),
                    ('published', 'Published'),
                    ('closed', 'Closed'),
                ],
                default='under_consideration',
                max_length=32,
            ),
        ),
        migrations.RunPython(set_existing_shows_closed, migrations.RunPython.noop),
    ]
