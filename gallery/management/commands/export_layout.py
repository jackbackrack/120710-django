"""Export a show's room layout (dims + supports + placements) to a JSON file.

    python manage.py export_layout <show-slug> --out layout-2026-07-22.json

The file can be restored later with `import_layout`. Handy as an off-site backup
before risky edits, or to move a layout between environments.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from gallery.models import Show
from gallery.views.room import _layout_payload


class Command(BaseCommand):
    help = "Export a show's layout (room, supports, placements) to a JSON file."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='Show slug (from the show URL).')
        parser.add_argument('--out', default=None,
                            help='Output file path. Defaults to <slug>-layout.json.')

    def handle(self, *args, **opts):
        slug = opts['slug']
        try:
            show = Show.objects.get(slug=slug)
        except Show.DoesNotExist:
            raise CommandError(f'No show with slug "{slug}".')
        payload = _layout_payload(show)
        out = opts['out'] or f'{slug}-layout.json'
        with open(out, 'w') as fh:
            json.dump(payload, fh, indent=2)
        self.stdout.write(self.style.SUCCESS(
            f'Exported {len(payload["placements"])} placements and '
            f'{len(payload["supports"])} supports for "{show.name}" → {out}'))
