"""Import Mailchimp audience exports into the local subscriber list.

    manage.py import_subscribers export.csv --dry-run
    manage.py import_subscribers export.csv --site 120710
    manage.py import_subscribers audience-1.csv audience-2.csv segment.csv

Mailchimp's per-status downloads carry no status column, so those are imported one status
at a time and told what they are:

    manage.py import_subscribers subscribed.csv   --status subscribed
    manage.py import_subscribers unsubscribed.csv --status unsubscribed
    manage.py import_subscribers cleaned.csv      --status cleaned

Order does not matter: an opt-out always wins, and a subscribed file never re-subscribes
somebody who has already said no.

The important part is the opt-out status. A Mailchimp export contains unsubscribed and
cleaned members alongside subscribed ones, and importing the lot as subscribed would mail
people who have already said no — which is both unlawful and the fastest way to earn spam
complaints on a brand-new sending domain. Those rows are imported as unsubscribed, so the
record of their choice survives the migration.

Several files can be given at once, and the same person may appear many times — across
exports, or twice within one after a Mailchimp merge. Every occurrence of an address is
collapsed into one person before anything is written, on two rules:

  * **Any opt-out anywhere wins.** Being listed as subscribed in one export does not undo
    having unsubscribed in another. The direction that could mail someone against their
    wishes is never the one chosen.
  * **The first name found is kept.** Exports frequently carry a name in one row and blank
    columns in another; taking the first non-empty means a blank row cannot erase it.

For a person already in the database, blank fields are filled in but existing ones are
left alone — a name someone has corrected here should survive an import of the export it
was corrected from.

All the files must belong to the same list; run the command once per `--site`.
"""
import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

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


def _has_column(fieldnames, keys):
    """Whether the header names one of `keys`.

    Checked against the header rather than the first row's value: an export whose first
    data row happens to have a blank address would otherwise be rejected as having no
    email column at all.
    """
    present = {(name or '').strip().lower() for name in (fieldnames or [])}
    return any(key in present for key in keys)


def _get(row, keys, default=''):
    for key in keys:
        for actual, value in row.items():
            if actual and actual.strip().lower() == key:
                return (value or '').strip()
    return default


class Command(BaseCommand):
    help = 'Import one or more Mailchimp CSV exports into the Subscriber table.'

    def add_arguments(self, parser):
        parser.add_argument('csv_paths', nargs='+', metavar='csv_path',
                            help='One or more exports belonging to the same list.')
        parser.add_argument('--site', help="Venue slug whose list this is. Omit for the "
                                           "network-wide list.")
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would happen and change nothing.')
        parser.add_argument(
            '--status', choices=sorted(SUBSCRIBED | OPTED_OUT),
            help="What these files are, for exports that carry no status column — "
                 "Mailchimp's per-status downloads do not. Applies to every file in this "
                 "run, so import one status at a time.")

    def handle(self, *args, **options):
        site = None
        if options['site']:
            site = Site.objects.filter(slug=options['site']).first()
            if site is None:
                raise CommandError(f'No site with slug {options["site"]!r}.')

        people, rows_read, unusable = self._collate(options['csv_paths'],
                                                    options.get('status'))
        if not people:
            raise CommandError('No usable rows in those files.')

        duplicates = rows_read - unusable - len(people)
        self.stdout.write(f'Read {rows_read} row(s) from {len(options["csv_paths"])} file(s) '
                          f'→ {len(people)} distinct people.')
        if duplicates:
            self.stdout.write(f'  {duplicates} duplicate row(s) merged.')
        if unusable:
            self.stdout.write(self.style.WARNING(
                f'  {unusable} row(s) had no usable email and were skipped.'))

        if options['dry_run']:
            self._report_dry_run(people, site)
            return

        with transaction.atomic():
            created, updated, filled, opted_out = self._write(people, site)

        where = site.name if site else 'the network-wide list'
        self.stdout.write(f'\nImported into {where}:')
        self.stdout.write(f'  {created} new, {updated} already present')
        if filled:
            self.stdout.write(f'  {filled} existing record(s) had a missing name filled in')
        self.stdout.write(f'  {opted_out} opted out and will not be mailed')
        total = Subscription.objects.filter(site=site, is_subscribed=True).count()
        self.stdout.write(self.style.SUCCESS(f'  {total} subscribed in total'))

    def _collate(self, paths, declared_status=None):
        """Every occurrence of an address folded into one record, before any writing.

        Returns ({email: {first_name, last_name, subscribed}}, rows_read, unusable).
        """
        people = {}
        rows_read = unusable = 0

        for path in paths:
            try:
                with open(path, newline='', encoding='utf-8-sig') as handle:
                    reader = csv.DictReader(handle)
                    fieldnames = reader.fieldnames
                    rows = list(reader)
            except OSError as exc:
                raise CommandError(f'Could not read {path}: {exc}')
            if not rows:
                self.stdout.write(self.style.WARNING(f'{path} has no rows.'))
                continue
            if not _has_column(fieldnames, EMAIL_KEYS):
                raise CommandError(
                    f'No email column in {path}. '
                    f'Columns are: {", ".join(fieldnames or [])}')

            # A file with no status column cannot say who opted out, and guessing is the one
            # mistake here that cannot be taken back: it mails people who said no. Mailchimp's
            # per-status downloads — subscribed.csv, unsubscribed.csv, cleaned.csv — are all
            # like this, so this is the normal case rather than an odd one.
            #
            # This used to default to subscribed, which imported every unsubscribed and
            # cleaned member as mailable and reported "0 opted out" while doing it.
            file_has_status = _has_column(fieldnames, STATUS_KEYS)
            if not file_has_status and declared_status is None:
                raise CommandError(
                    f'{path} has no status column, so there is no way to tell who opted '
                    f'out.\n'
                    f'Columns are: {", ".join(fieldnames or [])}\n\n'
                    f'Either export the whole audience as one file, which carries a status '
                    f'column,\nor say what this one is and import a status at a time:\n\n'
                    f'    manage.py import_subscribers subscribed.csv   --status subscribed\n'
                    f'    manage.py import_subscribers unsubscribed.csv --status unsubscribed\n'
                    f'    manage.py import_subscribers cleaned.csv      --status cleaned\n\n'
                    f'Order does not matter: an opt-out always wins, and importing a '
                    f'subscribed file\nnever re-subscribes somebody who has already said no.')

            for row in rows:
                rows_read += 1
                email = _get(row, EMAIL_KEYS).lower()
                if not email or '@' not in email:
                    unusable += 1
                    continue
                status = _get(row, STATUS_KEYS).lower() if file_has_status else ''
                if not status:
                    status = (declared_status or '').lower()
                # Anything not positively subscribed is treated as opted out, including a
                # blank — the safe direction for a value we cannot read.
                subscribed = status in SUBSCRIBED

                record = people.setdefault(
                    email, {'first_name': '', 'last_name': '', 'subscribed': True})
                # First non-empty name wins, so a blank column in a later row cannot
                # erase a name an earlier one supplied.
                for field, keys in (('first_name', FIRST_KEYS), ('last_name', LAST_KEYS)):
                    if not record[field]:
                        record[field] = _get(row, keys)
                # Any opt-out anywhere wins.
                record['subscribed'] = record['subscribed'] and subscribed

        return people, rows_read, unusable

    def _report_dry_run(self, people, site):
        existing = set(
            Subscription.objects
            .filter(site=site, subscriber__email__in=people)
            .values_list('subscriber__email', flat=True))
        opted_out = sum(1 for r in people.values() if not r['subscribed'])
        self.stdout.write('\nWould import into '
                          f'{site.name if site else "the network-wide list"}:')
        self.stdout.write(f'  {len(people) - len(existing)} new, {len(existing)} already present')
        self.stdout.write(f'  {opted_out} opted out and would not be mailed')
        self.stdout.write(self.style.WARNING('  Nothing was written (--dry-run).'))

    def _write(self, people, site):
        created = updated = filled = opted_out = 0

        for email, record in people.items():
            subscriber, made = Subscriber.objects.get_or_create(
                email=email,
                defaults={'first_name': record['first_name'],
                          'last_name': record['last_name']})
            if not made:
                # Fill gaps only. A name corrected here outranks the export it came from.
                missing = [f for f in ('first_name', 'last_name')
                           if not getattr(subscriber, f) and record[f]]
                if missing:
                    for field in missing:
                        setattr(subscriber, field, record[field])
                    subscriber.save(update_fields=missing + ['updated_at'])
                    filled += 1

            subscription, was_created = Subscription.objects.get_or_create(
                subscriber=subscriber, site=site,
                defaults={'source': Subscription.SOURCE_IMPORT,
                          'is_subscribed': record['subscribed'],
                          'unsubscribed_reason': ('' if record['subscribed']
                                                  else Subscription.UNSUB_REQUESTED)})
            if was_created:
                created += 1
            else:
                updated += 1

            if not record['subscribed']:
                opted_out += 1
                # Never re-subscribe someone the export says opted out, even if a row for
                # them already existed as subscribed. Their most recent choice wins.
                if subscription.is_subscribed:
                    subscription.unsubscribe(reason=Subscription.UNSUB_REQUESTED)

        return created, updated, filled, opted_out
