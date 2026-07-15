import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from gallery.models import ArtistSchedule, Artist, ScheduleWindow, Show
from gallery.models.logistics import DROPOFF, INSTALL, PICKUP, KIND_CHOICES
from gallery.permissions import can_manage_show

_KIND_LABEL = dict(KIND_CHOICES)


def _arrival_kind(show):
    """Whether the artist's 'bring your work' event is an install or a drop-off."""
    return INSTALL if show.self_install else DROPOFF


def _show_kinds(show):
    """The scheduling kinds relevant to this show, in display order."""
    return [_arrival_kind(show), PICKUP]


def _accepted_artists(show):
    return Artist.objects.filter(artworks__shows=show).distinct().order_by('name')


def _parse_time(value):
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.datetime.strptime(value, fmt).time()
        except (ValueError, TypeError):
            continue
    return None


# ── Curator: define drop-off/install + pickup windows ─────────────────────────
@login_required
def show_schedule_windows(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    kinds = _show_kinds(show)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            kind = request.POST.get('kind')
            date = request.POST.get('date')
            start = _parse_time(request.POST.get('start'))
            end = _parse_time(request.POST.get('end'))
            if kind in kinds and date and start and end and start < end:
                ScheduleWindow.objects.create(show=show, kind=kind, date=date, start=start, end=end)
            else:
                messages.error(request, 'Please provide a date and a valid start/end time.')
        elif action == 'delete':
            ScheduleWindow.objects.filter(show=show, pk=request.POST.get('window_id')).delete()
        return redirect('gallery:show_schedule_windows', slug=show.slug)

    windows = list(show.schedule_windows.all())
    context = {
        'show': show,
        'sections': [
            {'kind': k, 'label': _KIND_LABEL[k], 'windows': [w for w in windows if w.kind == k]}
            for k in kinds
        ],
    }
    return render(request, 'gallery/show_schedule_windows.html', context)


# ── Artist: choose a drop-off/install + pickup time ───────────────────────────
@login_required
def artist_schedule(request, slug):
    show = get_object_or_404(Show, slug=slug)
    artist = Artist.objects.filter(user=request.user, artworks__shows=show).distinct().first()
    if artist is None:
        raise Http404   # only artists with work in the show may schedule

    kinds = _show_kinds(show)
    windows_by_kind = {k: list(show.schedule_windows.filter(kind=k)) for k in kinds}

    if request.method == 'POST':
        kind = request.POST.get('kind')
        if kind in kinds:
            window = show.schedule_windows.filter(kind=kind, pk=request.POST.get('window_id')).first()
            t = _parse_time(request.POST.get('time'))
            if window and t and window.start <= t <= window.end:
                sched, _ = ArtistSchedule.objects.get_or_create(show=show, artist=artist, kind=kind)
                sched.window = window
                sched.scheduled_time = t
                sched.save(update_fields=['window', 'scheduled_time'])
                messages.success(request, f'Your {_KIND_LABEL[kind].lower()} time is set.')
            else:
                messages.error(request, 'Please pick a window and a time within it.')
        return redirect('gallery:artist_schedule', slug=show.slug)

    existing = {s.kind: s for s in ArtistSchedule.objects.filter(show=show, artist=artist)}
    context = {
        'show': show,
        'artist': artist,
        'kinds': [
            {'kind': k, 'label': _KIND_LABEL[k], 'windows': windows_by_kind[k], 'current': existing.get(k)}
            for k in kinds if windows_by_kind[k]
        ],
    }
    return render(request, 'gallery/artist_schedule.html', context)


# ── Curator: tracker / check-off ──────────────────────────────────────────────
@login_required
def show_schedule_tracker(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    kinds = _show_kinds(show)
    if request.method == 'POST':
        artist = Artist.objects.filter(pk=request.POST.get('artist_id')).first()
        kind = request.POST.get('kind')
        if artist and kind in kinds:
            sched, _ = ArtistSchedule.objects.get_or_create(show=show, artist=artist, kind=kind)
            sched.done = request.POST.get('done') == '1'
            sched.done_at = timezone.now() if sched.done else None
            sched.done_by = request.user if sched.done else None
            sched.save(update_fields=['done', 'done_at', 'done_by'])
        return redirect('gallery:show_schedule_tracker', slug=show.slug)

    artists = list(_accepted_artists(show))
    sched_map = {}
    for s in ArtistSchedule.objects.filter(show=show).select_related('window'):
        sched_map[(s.artist_id, s.kind)] = s

    rows = [
        {'artist': a, 'cells': [{'kind': k, 'sched': sched_map.get((a.id, k))} for k in kinds]}
        for a in artists
    ]
    columns = []
    for k in kinds:
        done = sum(1 for a in artists if sched_map.get((a.id, k)) and sched_map[(a.id, k)].done)
        columns.append({'kind': k, 'label': _KIND_LABEL[k], 'done': done, 'total': len(artists),
                        'left': len(artists) - done})

    return render(request, 'gallery/show_schedule_tracker.html',
                  {'show': show, 'rows': rows, 'columns': columns, 'colspan': 1 + 2 * len(columns)})
