from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('reviews', '0004_scoring_0_to_100'),
    ]

    operations = [
        migrations.RenameField(
            model_name='rubriccriterion',
            old_name='weight',
            new_name='percentage',
        ),
    ]
