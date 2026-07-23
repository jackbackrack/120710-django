"""Restore a show's room layout from a JSON file written by `export_layout`.

    python manage.py import_layout <show-slug> layout-2026-07-22.json

This REPLACES the show's current placements/supports. An automatic snapshot of the
current layout is taken first (visible under Snapshots in the layout tool), so the
restore is itself reversible.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from gallery.models import Show
from gallery.views.room import _apply_layout, _take_snapshot


class Command(BaseCommand):
    help = "Restore a show's layout from a JSON file (replaces current placements)."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='Show slug (from the show URL).')
        parser.add_argument('path', help='Path to a JSON file from export_layout.')

    def handle(self, *args, **opts):
        slug = opts['slug']
        try:
            show = Show.objects.get(slug=slug)
        except Show.DoesNotExist:
            raise CommandError(f'No show with slug "{slug}".')
        try:
            with open(opts['path']) as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise CommandError(f'Could not read layout file: {e}')

        # Safety net first: snapshot what we're about to overwrite.
        from gallery.models import ShowLayoutSnapshot
        _take_snapshot(show, user=None, kind=ShowLayoutSnapshot.AUTO)

        errors = _apply_layout(show, data)
        n_p = len(data.get('placements', []))
        n_s = len(data.get('supports', []))
        self.stdout.write(self.style.SUCCESS(
            f'Restored {n_p} placements and {n_s} supports to "{show.name}".'))
        if errors:
            self.stdout.write(self.style.WARNING(
                f'{len(errors)} item(s) skipped: ' + '; '.join(errors[:10])))
