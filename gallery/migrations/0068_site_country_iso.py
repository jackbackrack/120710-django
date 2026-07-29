from django.db import migrations
import django_countries.fields

# Free text to ISO 3166 alpha-2, before the column narrows to two characters. Without
# this the AlterField would truncate "USA" to "US" by luck and anything else to nonsense.
ALIASES = {
    'us': 'US', 'usa': 'US', 'u.s.': 'US', 'u.s.a.': 'US',
    'united states': 'US', 'united states of america': 'US',
    'uk': 'GB', 'united kingdom': 'GB', 'great britain': 'GB',
    'canada': 'CA', 'mexico': 'MX',
}


def to_iso(apps, schema_editor):
    Site = apps.get_model('gallery', 'Site')
    unknown = []
    for site in Site.objects.all():
        raw = (site.country or '').strip()
        if not raw:
            code = 'US'
        elif len(raw) == 2 and raw.isalpha():
            code = raw.upper()
        else:
            code = ALIASES.get(raw.lower())
        if code is None:
            unknown.append((site.pk, site.name, raw))
            continue
        if code != site.country:
            site.country = code
            site.save(update_fields=['country'])
    if unknown:
        # Refusing rather than blanking. The column is about to narrow to two characters,
        # so an unrecognised value has to become *something*, and the tempting default —
        # '' — loses the only record of where the venue is, silently, during a deploy.
        # Postgres DDL is transactional, so raising here rolls the whole thing back and
        # nothing is half-migrated. Fix the data (or extend ALIASES) and migrate again.
        listing = '\n'.join(f'  site {pk} ({name!r}): {raw!r}' for pk, name, raw in unknown)
        raise RuntimeError(
            f'{len(unknown)} site(s) have a country this migration cannot map to an ISO '
            f'3166 alpha-2 code:\n{listing}\n\n'
            f'Set them to a two-letter code (or a name listed in ALIASES in this '
            f'migration) and run migrate again. Nothing has been changed.')


def back_to_text(apps, schema_editor):
    Site = apps.get_model('gallery', 'Site')
    Site.objects.filter(country='US').update(country='USA')


class Migration(migrations.Migration):

    dependencies = [('gallery', '0067_submission_area_scope')]

    operations = [
        migrations.RunPython(to_iso, back_to_text),
        migrations.AlterField(
            model_name='site',
            name='country',
            field=django_countries.fields.CountryField(default='US', max_length=2),
        ),
    ]
