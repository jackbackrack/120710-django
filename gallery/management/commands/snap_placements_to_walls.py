"""Snap wall-hung placements back onto their wall plane after a room resize.

    python manage.py snap_placements_to_walls full-feel            # report only
    python manage.py snap_placements_to_walls full-feel --apply    # write
    python manage.py snap_placements_to_walls --apply              # every show

For a piece on a vertical wall, the coordinate perpendicular to that wall (x_in on
E/W, z_in on N/S) is redundant with `wall` — it must equal ±width_in/2 or
±depth_in/2. When a site's room dimensions change, the layout editor only rewrites
that coordinate for pieces it actually moves, so untouched pieces keep the old
wall's value and end up outside the room. The 3D viewer then hides them entirely
behind the opaque wall (the 2D views never read this axis, so they look fine).

This command rewrites the stale coordinate to the current wall plane. Along-wall
position (the axis you actually place on) and height are never touched, so nothing
moves relative to its wall. Floor/ceiling pieces are skipped — both of their
horizontal coordinates are along-surface, and y_in carries pedestal height.

Safety: dry-run unless --apply. With --apply, each affected show gets a named MANUAL
snapshot first (restorable from the layout editor's snapshot panel) and its writes run
in one transaction. If the snapshot cannot be taken the show is left alone, unless you
pass --no-snapshot.

Usually you do not need this command: a save from the layout editor derives the same
coordinate (see `wall_plane_coord`), so opening the layout and letting it autosave
fixes a show. This is for auditing, or for fixing many shows without opening each.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gallery.models import Show, ShowLayoutSnapshot, Support, WallPlacement
from gallery.views.room import _room_config, _take_snapshot, wall_plane_coord


class Command(BaseCommand):
    help = ('Snap wall-hung placements (and wall shelves) onto their current wall '
            'plane after a room resize. Dry-run unless --apply is given.')

    def add_arguments(self, parser):
        parser.add_argument('slug', nargs='?', default=None,
                            help='Show slug. Omit to process every show.')
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes. Without this, only report them.')
        parser.add_argument('--tolerance', type=float, default=0.01,
                            help='Inches of drift tolerated before snapping (default 0.01).')
        parser.add_argument('--no-snapshot', action='store_true',
                            help='Skip the pre-write restore point. Only for when you '
                                 'have already backed up with export_layout.')

    def handle(self, *args, **opts):
        slug, apply_, tol = opts['slug'], opts['apply'], opts['tolerance']
        self.no_snapshot = opts['no_snapshot']

        if slug:
            try:
                shows = [Show.objects.get(slug=slug)]
            except Show.DoesNotExist:
                raise CommandError(f'No show with slug "{slug}".')
        else:
            shows = list(Show.objects.all().order_by('slug'))

        total = 0
        for show in shows:
            total += self._handle_show(show, apply_, tol)

        if not total:
            self.stdout.write(self.style.SUCCESS('Nothing stale — every wall item is on its wall.'))
        elif apply_:
            self.stdout.write(self.style.SUCCESS(f'Snapped {total} item(s) onto their wall plane.'))
        else:
            self.stdout.write(self.style.WARNING(
                f'{total} item(s) would move. Re-run with --apply to write.'))
        return None

    def _handle_show(self, show, apply_, tol):
        config, _site = _room_config(show)
        if config is None:
            return 0   # no site → no room dimensions to snap against

        rows = []   # (obj, field, old, new, label)
        for wp in (WallPlacement.objects.filter(show=show)
                   .select_related('artwork').order_by('wall', 'x_in', 'z_in')):
            r = self._check(wp, config, tol, str(wp.artwork))
            if r:
                rows.append(r)
        for s in Support.objects.filter(show=show):
            r = self._check(s, config, tol, s.label or f'support {s.pk}')
            if r:
                rows.append(r)

        if not rows:
            return 0

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{show.slug} — room {config.width_in:g}×{config.depth_in:g} in '
            f'({len(rows)} stale)'))
        for obj, field, old, new, label in rows:
            self.stdout.write(
                f'  {obj.wall:>2}  {field}  {old:>9.2f} → {new:>8.2f}   {label}')
        if not apply_:
            return len(rows)

        # A restore point BEFORE touching anything. Deliberately MANUAL, not auto:
        # auto snapshots roll off after MAX_AUTO_SNAPSHOTS saves, and this rewrite
        # should stay recoverable however much editing happens afterwards.
        if not self.no_snapshot:
            snap = _take_snapshot(show, None, kind=ShowLayoutSnapshot.MANUAL,
                                  name=f'before snap-to-walls ({len(rows)} item(s))')
            if snap is None:
                raise CommandError(
                    f'Could not snapshot "{show.slug}" before writing, so it was left '
                    f'unchanged (see logs for why). Back it up with '
                    f'`export_layout {show.slug}` and re-run with --no-snapshot to '
                    f'proceed anyway.')
            self.stdout.write(f'  ↳ restore point saved: "{snap.name}" (snapshot #{snap.pk})')

        # One transaction per show: a failure part-way leaves that show untouched
        # rather than half-converted.
        with transaction.atomic():
            for obj, field, old, new, label in rows:
                setattr(obj, field, new)
                obj.save(update_fields=[field])
        return len(rows)

    def _check(self, obj, config, tol, label):
        """Return (obj, field, old, new, label) if obj's perpendicular coord is
        stale, else None."""
        snap = wall_plane_coord(obj.wall, config)
        if snap is None:
            return None            # floor / ceiling: no perpendicular axis
        field, new = snap
        old = getattr(obj, field)
        if abs(old - new) <= tol:
            return None
        return (obj, field, old, new, label)
