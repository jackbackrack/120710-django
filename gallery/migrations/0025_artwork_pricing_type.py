import re
from django.db import migrations, models


NFS_RE = re.compile(r'\b(nfs|n\.f\.s\.?|not[\s\-]for[\s\-]sale)\b', re.I)
ON_REQUEST_RE = re.compile(
    r'\b(upon\s+request|on\s+request|poa|p\.o\.a\.?|price\s+on\s+(?:application|request)|por)\b', re.I
)
BEST_OFFER_RE = re.compile(r'\b(best\s+offer|make\s+an?\s+offer|or\s+best\s+offer|obo)\b', re.I)


def migrate_pricing_forward(apps, schema_editor):
    Artwork = apps.get_model('gallery', 'Artwork')
    for artwork in Artwork.objects.all():
        text = (artwork.pricing or '').strip()
        if NFS_RE.search(text):
            artwork.pricing_type = 'nfs'
        elif ON_REQUEST_RE.search(text):
            artwork.pricing_type = 'on_request'
        elif BEST_OFFER_RE.search(text):
            artwork.pricing_type = 'best_offer'
        elif not text and artwork.price is not None:
            artwork.pricing_type = 'for_sale'
        else:
            artwork.pricing_type = 'on_request'
        artwork.save(update_fields=['pricing_type'])


def migrate_pricing_backward(apps, schema_editor):
    Artwork = apps.get_model('gallery', 'Artwork')
    labels = {
        'nfs': 'NFS',
        'on_request': 'Upon Request',
        'best_offer': 'Best Offer',
        'for_sale': '',
    }
    for artwork in Artwork.objects.all():
        artwork.pricing = labels.get(artwork.pricing_type, '')
        artwork.save(update_fields=['pricing'])


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0024_remove_submission_statement'),
    ]

    operations = [
        migrations.AddField(
            model_name='artwork',
            name='pricing_type',
            field=models.CharField(
                choices=[
                    ('for_sale', 'For Sale'),
                    ('on_request', 'Price on Request'),
                    ('best_offer', 'Best Offer'),
                    ('nfs', 'Not For Sale'),
                ],
                default='on_request',
                max_length=20,
                verbose_name='Pricing',
            ),
        ),
        migrations.RunPython(migrate_pricing_forward, migrate_pricing_backward),
        migrations.RemoveField(
            model_name='artwork',
            name='pricing',
        ),
        migrations.AlterField(
            model_name='artwork',
            name='price',
            field=models.FloatField(blank=True, null=True, verbose_name='Price ($)'),
        ),
    ]
