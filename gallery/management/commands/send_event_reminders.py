"""Remind people the day before an event they said they were coming to.

    manage.py send_event_reminders --dry-run
    manage.py send_event_reminders

Meant for a daily scheduled run. This is the piece that actually changes turnout: one
announcement three weeks out is a single shot at a date nobody has planned around yet, and what
goes wrong between that email and the night is almost always forgetting.

Only people who replied are mailed, which is the whole reason the RSVP exists. A reminder to the
full mailing list would be a second campaign; a reminder to somebody who said they were coming is
a service they asked for. "Maybe" is included on purpose — they have not decided, and the night
before is when they will.

Safe to run more than once a day. Each reminder is marked as it goes, so a cron that fires twice
or a re-run after a deploy does not mail anybody the same thing again.
"""
from django.core.management.base import BaseCommand

from gallery import rsvps as engine


class Command(BaseCommand):
    help = 'Email tomorrow\'s event reminders to people who said yes or maybe.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report who would be reminded and send nothing.')

    def handle(self, *args, **options):
        due = list(engine.due_for_reminder())
        if not due:
            self.stdout.write('Nothing to remind anybody about.')
            return

        by_event = {}
        for rsvp in due:
            by_event.setdefault(rsvp.event, []).append(rsvp)

        for event, rsvps in by_event.items():
            heads = sum(r.party_size for r in rsvps if r.is_coming)
            self.stdout.write(
                f'{event.name} — {event.date} {event.time_range}: '
                f'{len(rsvps)} to remind, {heads} expected')
            for rsvp in rsvps:
                self.stdout.write(f'    {rsvp.response:<6} {rsvp.email}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Nothing was sent (--dry-run).'))
            return

        sent = sum(1 for rsvp in due if engine.send_reminder(rsvp))
        failed = len(due) - sent
        self.stdout.write(self.style.SUCCESS(f'Sent {sent} reminder(s).'))
        if failed:
            # Not marked as reminded, so the next run picks them up rather than losing them.
            self.stdout.write(self.style.WARNING(
                f'{failed} could not be sent and will be retried on the next run.'))
