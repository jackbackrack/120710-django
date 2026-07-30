"""The shows-and-events agenda, and the iCalendar feed of it.

Both take the same optional site scope as the listing views, so /calendar/ is the network's
and /site/<slug>/calendar/ is one venue's. See gallery/calendars.py for the timeline itself.
"""
import datetime as dt

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import condition, require_safe

from gallery import calendars
from gallery.views.mixins import visible_site_or_404


def _scope(request, site_slug):
    return visible_site_or_404(request, site_slug) if site_slug else None


def _rows(entries):
    """Pair each entry with a month heading, or None when it repeats the one above."""
    rows, last = [], None
    for entry in entries:
        month = entry.sort_date.replace(day=1)
        rows.append({'entry': entry, 'month': month if month != last else None})
        last = month
    return rows


@require_safe
def calendar_view(request, site_slug=None):
    """Every show and event on one page, upcoming first and then back through the past.

    Deliberately not paginated and not lazily loaded. An earlier version split it into
    Upcoming and Past with a link between them, which put a navigation decision in front of a
    reader who only wanted to see the programme. One page has no such seam, and the whole
    thing is scrollable and findable with the browser's own search.

    The cost is that the response grows with the archive. Fine at the current scale and for a
    long time yet; if a gallery's history reaches the point where this page is slow, the
    answer is to load the past half on demand, and the split will have earned its place by
    then rather than being imposed up front.
    """
    site = _scope(request, site_slug)
    entries = calendars.timeline(site=site, user=request.user)
    today = dt.date.today()

    # Upcoming ascending, then the past most-recent-first. Not one strict chronology: that
    # would bury what is on now under every show the gallery has ever hung. The past is
    # ordered by show rather than reversed outright — see calendars.archive_order.
    upcoming = [e for e in entries if e.end_date >= today]
    past = [e for e in entries if e.end_date < today]

    feed_url = (reverse('gallery:site_shows_ics', kwargs={'site_slug': site.slug})
                if site else reverse('gallery:shows_ics'))
    return render(request, 'public/calendar.html', {
        'site': site,
        'upcoming_rows': _rows(upcoming),
        'past_rows': _rows(calendars.archive_order(past)),
        'today': today,
        'feed_url': feed_url,
        # webcal:// makes a click subscribe rather than download a snapshot.
        'feed_webcal': 'webcal://' + request.get_host() + feed_url,
    })


def _feed_last_modified(request, site_slug=None):
    """For the conditional-GET decorator, so polling clients can 304.

    Calendar clients refresh on their own schedule and ignore any preference of ours, so the
    cheapest thing available is to make the repeat requests free.
    """
    site = visible_site_or_404(request, site_slug) if site_slug else None
    return calendars.last_modified(calendars.timeline(site=site, user=None))


@require_safe
@condition(last_modified_func=_feed_last_modified)
def shows_ics(request, site_slug=None):
    """The public calendar feed.

    Deliberately *not* sent as an attachment — unlike the per-artist schedule download,
    which is a one-off. A subscribable feed with Content-Disposition saves a dead snapshot
    in most clients instead of subscribing.

    user=None on purpose: this URL is unauthenticated and cacheable, so it shows exactly
    what an anonymous visitor may see, never a draft, whoever happens to be signed in.
    """
    site = _scope(request, site_slug)
    entries = calendars.timeline(site=site, user=None)
    name = f'{site.name} — shows and events' if site else 'Shows and events'
    body = calendars.feed(
        entries, name=name, domain=request.get_host().split(':')[0],
        site=site, url=request.build_absolute_uri())
    response = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'inline; filename="shows.ics"'
    return response
