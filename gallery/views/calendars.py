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


@require_safe
def calendar_view(request, site_slug=None):
    """Shows and events on one timeline, month by month.

    Past entries are included but collapsed behind a toggle: a gallery's history is worth
    browsing, and it should not be the first thing on the page.
    """
    site = _scope(request, site_slug)
    entries = calendars.timeline(site=site, user=request.user)
    today = dt.date.today()

    upcoming = [e for e in entries if e.end_date >= today]
    past = [e for e in entries if e.end_date < today]

    feed_url = (reverse('gallery:site_shows_ics', kwargs={'site_slug': site.slug})
                if site else reverse('gallery:shows_ics'))
    return render(request, 'public/calendar.html', {
        'site': site,
        'upcoming_months': calendars.group_by_month(upcoming),
        'past_months': list(reversed(calendars.group_by_month(past))),
        'past_count': len(past),
        'feed_url': feed_url,
        # webcal:// makes a click subscribe rather than download a snapshot.
        'feed_webcal': 'webcal://' + request.get_host() + feed_url,
        'today': today,
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
