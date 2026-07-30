"""Import a Mailchimp audience export into the local subscriber list.

    manage.py import_subscribers export.csv --dry-run
    manage.py import_subscribers export.csv --site 120710

The important part is the opt-out status. A Mailchimp export contains unsubscribed and
cleaned members alongside subscribed ones, and importing the lot as subscribed would mail
people who have already said no — which is both unlawful and the fastest way to earn spam
complaints on a brand-new sending domain. Those rows are imported as unsubscribed, so the
record of their choice survives the migration.
"""
import csv

from django.core.management.base import BaseCommand, CommandError

from gallery.models import Site, Subscriber, Subscription

# Mailchimp's own spellings. Anything not in the subscribed set is treated as opted out —
# the safe direction for a value we do not recognise.
SUBSCRIBED = {'subscribed'}
OPTED_OUT = {'unsubscribed', 'cleaned', 'pending', 'transactional', 'archived'}

# Mailchimp has renamed these across export formats; accept them all rather than making
# the operator rename columns.
EMAIL_KEYS = ['email address', 'email_address', 'email']
FIRST_KEYS = ['first name', 'first_name', 'fname']
LAST_KEYS = ['last name', 'last_name', 'lname']
STATUS_KEYS = ['status', 'member status', 'member_status', 'subscription status']


def _get(row, keys, default=''):
    for key in keys:
        for actual, value in row.items():
            if actual and actual.strip().lower() == key:
                return (value or '').strip()
    return default


class Command(BaseCommand):
    help = 'Import a Mailchimp CSV export into the Subscriber table.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--site', help="Venue slug whose list this is. Omit for the "
                                           "network-wide list.")
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would happen and change nothing.')

    def handle(self, *args, **options):
        site = None
        if options['site']:
            site = Site.objects.filter(slug=options['site']).first()
            if site is None:
                raise CommandError(f'No site with slug {options["site"]!r}.')

        try:
            with open(options['csv_path'], newline='', encoding='utf-8-sig') as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CommandError(f'Could not read {options["csv_path"]}: {exc}')

        if not rows:
            raise CommandError('That file has no rows.')
        if not _get(rows[0], EMAIL_KEYS):
            raise CommandError(
                f'No email column found. Columns are: {", ".join(rows[0].keys())}')

        created = updated = opted_out = skipped = 0
        for row in rows:
            email = _get(row, EMAIL_KEYS).lower()
            if not email or '@' not in email:
                skipped += 1
                continue
            status = _get(row, STATUS_KEYS).lower()
            subscribed = status in SUBSCRIBED if status else True

            if options['dry_run']:
                exists = Subscription.objects.filter(
                    subscriber__email=email, site=site).exists()
                if exists:
                    updated += 1
                else:
                    created += 1
                if not subscribed:
                    opted_out += 1
                continue

            subscriber, _ = Subscriber.objects.get_or_create(
                email=email,
                defaults={'first_name': _get(row, FIRST_KEYS),
                          'last_name': _get(row, LAST_KEYS)})
            subscription, was_created = Subscription.objects.get_or_create(
                subscriber=subscriber, site=site,
                defaults={'source': Subscription.SOURCE_IMPORT,
                          'is_subscribed': subscribed,
                          'unsubscribed_reason': ('' if subscribed
                                                  else Subscription.UNSUB_REQUESTED)})
            if was_created:
                created += 1
            else:
                updated += 1
            if not subscribed:
                opted_out += 1
                # Never re-subscribe someone the export says opted out, even if a row for
                # them already existed as subscribed. Their most recent choice wins.
                if subscription.is_subscribed:
                    subscription.unsubscribe(reason=Subscription.UNSUB_REQUESTED)

        where = site.name if site else 'the network-wide list'
        prefix = 'Would import' if options['dry_run'] else 'Imported'
        self.stdout.write(f'{prefix} into {where}:')
        self.stdout.write(f'  {created} new, {updated} already present')
        self.stdout.write(f'  {opted_out} of those are opted out and will not be mailed')
        if skipped:
            self.stdout.write(self.style.WARNING(f'  {skipped} row(s) had no usable email'))
        if not options['dry_run']:
            total = Subscription.objects.filter(site=site, is_subscribed=True).count()
            self.stdout.write(self.style.SUCCESS(f'  {total} subscribed in total'))
