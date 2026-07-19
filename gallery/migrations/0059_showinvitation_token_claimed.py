import secrets

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import gallery.models.exhibitions


def gen_tokens(apps, schema_editor):
    # Give each existing invitation its own unique token before the unique
    # constraint is applied (AddField gives them all the same default).
    ShowInvitation = apps.get_model('gallery', 'ShowInvitation')
    for inv in ShowInvitation.objects.all():
        inv.token = secrets.token_urlsafe(32)
        inv.save(update_fields=['token'])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('gallery', '0058_showinvitation_email_sent_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='showinvitation',
            name='token',
            field=models.CharField(
                default=gallery.models.exhibitions._gen_invite_token,
                editable=False, max_length=64),
        ),
        migrations.RunPython(gen_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='showinvitation',
            name='token',
            field=models.CharField(
                default=gallery.models.exhibitions._gen_invite_token,
                editable=False, max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name='showinvitation',
            name='claimed_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='claimed_invitations', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='showinvitation',
            name='claimed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
