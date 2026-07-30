"""Send or finish a campaign from the command line.

    manage.py send_campaign 12 --dry-run
    manage.py send_campaign 12
    manage.py send_campaign 12 --resume
    manage.py send_campaign 12 --limit 100           # day one of a warm-up
    manage.py send_campaign 12 --resume --limit 300  # day two, and so on

The web page starts sends in a background thread, which is enough for the ordinary case and
gives the operator a progress bar. This exists for the cases that thread cannot cover:

  * A send interrupted by a deploy. The thread went with the process; this finishes the job
    without racing whatever restarted.
  * A list large enough that minutes of sending inside a web container is the wrong place
    for it.
  * Diagnosing a failure with the provider's error in front of you rather than in a log.
  * Warming up a new sending domain, with --limit: a domain with no sending history that puts a
    thousand messages out on its first day is filtered on volume however gently they are paced,
    so the ramp has to be across days. Each pass leaves the campaign paused with the rest still
    owed. Note that a second pass needs --resume as well as --limit; being told "there is
    nothing to resume" is better than a stray --limit re-sending a finished campaign.

It is the same engine and the same guards, so it cannot send an untested draft either, and
`--resume` mails only the people with no delivery record.
"""
from django.core.management.base import BaseCommand, CommandError

from gallery import campaigns as engine
from gallery.models import Campaign


class Command(BaseCommand):
    help = 'Send a campaign, or finish one that stopped part-way.'

    def add_arguments(self, parser):
        parser.add_argument('campaign_id', type=int)
        parser.add_argument('--resume', action='store_true',
                            help='Finish a send that failed or was interrupted, skipping '
                                 'everyone already sent it.')
        parser.add_argument('--limit', type=int,
                            help='Send at most this many, then pause with the rest still owed. '
                                 'For warming up a new sending domain across several days.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report who would be mailed and send nothing.')

    def handle(self, *args, **options):
        campaign = Campaign.objects.filter(pk=options['campaign_id']).first()
        if campaign is None:
            raise CommandError(f'No campaign with id {options["campaign_id"]}.')

        where = campaign.site.name if campaign.site else 'the network-wide list'
        owed = campaign.remaining_count
        self.stdout.write(f'{campaign.subject!r} → {where}')
        self.stdout.write(f'  {campaign.sent_so_far} already sent, {owed} still to go '
                          f'(status: {campaign.get_status_display().lower()})')

        if options['limit']:
            self.stdout.write(f'  sending at most {options["limit"]} this pass')

        if options['dry_run']:
            for subscription in engine.pending(campaign)[:10]:
                self.stdout.write(f'    {subscription.subscriber.email}')
            if owed > 10:
                self.stdout.write(f'    … and {owed - 10} more')
            self.stdout.write(self.style.WARNING('Nothing was sent (--dry-run).'))
            return

        if not owed:
            self.stdout.write(self.style.SUCCESS('Everyone on the list already has it.'))
            return

        try:
            sent = engine.send_campaign(campaign, resume=options['resume'],
                                        limit=options['limit'])
        except ValueError as exc:
            # A refused guard, not a provider failure — say what to do rather than traceback.
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(
            f'Sent {sent} message(s). {campaign.sent_so_far} of '
            f'{campaign.sent_so_far + campaign.remaining_count} have it now.'))
