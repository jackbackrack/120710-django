"""Staff view of who is coming, and the calendar feed they can subscribe to.

The list is read-only on purpose. A visit is the visitor's to change — they have a cancellation
link — and a gallery that silently cancels somebody's booking without telling them is worse than
one that emails to say sorry.
"""
import csv
import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST, require_safe

from gallery import calendars
from gallery.calendars import site_timezone
from gallery.models import Event, EventRsvp, Site, Visit
from gallery.permissions import (directed_site_ids, is_curator_user, is_site_director,
                                 is_staff_user)

# Enough to see a pattern across a season without turning the page into an archive.
PAST_EVENT_LIMIT = 25


def _may_see(user):
    # Curator is derived from curated shows, not from a Django group: this used to check
    # `groups.filter(name='curator')`, which was a second, disagreeing definition of the
    # same role.
    return is_staff_user(user) or is_curator_user(user) or is_site_director(user)


def _visible_site_ids(user):
    """Which venues' bookings and replies this user may see, or None for all of them.

    Staff and curators see everything, as they always have. A director sees their own
    venue and no other — without this the page would hand them every other venue's
    visitors by name.
    """
    if is_staff_user(user) or is_curator_user(user):
        return None
    return directed_site_ids(user)


def _events_with_replies(date_filter, order, limit=None, site_ids=None):
    """Events somebody has replied to, replies prefetched.

    A join and a `distinct()` rather than a per-event `exists()`: the page renders every reply
    anyway, so this is one query and a prefetch instead of one round trip per event.
    """
    events = (Event.objects.filter(**date_filter)
              .filter(rsvps__isnull=False)
              .select_related('show').prefetch_related('rsvps')
              .order_by(order))
    if site_ids is not None:
        events = events.filter(show__sites__pk__in=site_ids)
    events = events.distinct()
    return list(events[:limit] if limit else events)


@login_required
def visit_list(request):
    if not _may_see(request.user):
        raise Http404

    now = dj_timezone.now()
    site_ids = _visible_site_ids(request.user)
    upcoming = (Visit.objects.filter(when__gte=now, cancelled_at__isnull=True)
                .select_related('site'))
    recent = (Visit.objects.filter(when__lt=now).select_related('site')
              .order_by('-when'))
    if site_ids is not None:
        upcoming = upcoming.filter(site__pk__in=site_ids)
        recent = recent.filter(site__pk__in=site_ids)
    recent = recent[:25]

    # A director gets their own venue's calendar address; staff get every venue's.
    feeds = []
    feed_sites = Site.objects.filter(visits_enabled=True).order_by('name')
    if site_ids is not None:
        feed_sites = feed_sites.filter(pk__in=site_ids)
    if is_staff_user(request.user) or is_site_director(request.user):
        for site in feed_sites:
            if not site.visit_feed_token:
                site.save(update_fields=['visit_feed_token'])
            feeds.append((site, request.build_absolute_uri(
                f'/visits/{site.visit_feed_token}.ics')))

    # Upcoming events with replies, so the gallery knows what to cater for — and past ones, so
    # what was catered for can be compared against what happened. A reply that vanishes the
    # morning after is a season's worth of turnout thrown away every year.
    rsvp_events = _events_with_replies({'date__gte': now.date()}, 'date',
                                       site_ids=site_ids)
    past_rsvp_events = _events_with_replies(
        {'date__lt': now.date()}, '-date', limit=PAST_EVENT_LIMIT, site_ids=site_ids)

    return render(request, 'gallery/visit_list.html', {
        'rsvp_events': rsvp_events,
        'past_rsvp_events': past_rsvp_events,
        'upcoming': [(v, v.when.astimezone(site_timezone(v.site))) for v in upcoming],
        'recent': [(v, v.when.astimezone(site_timezone(v.site))) for v in recent],
        'feeds': feeds,
    })


def _csv_safe(value):
    """Stop a spreadsheet treating somebody's reply as a formula.

    Name and note come from a public form, and Excel and Numbers execute a cell beginning `=`,
    `+`, `-` or `@` when the file is opened. Prefixing an apostrophe is the standard defusing;
    it is invisible in the cell.
    """
    text = '' if value is None else str(value)
    return f"'{text}" if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@login_required
@require_safe
def rsvp_csv(request):
    """The door list on the night, and the record of it afterwards.

    One event with `?event=<pk>`, otherwise every reply there is. Both are the same columns, so
    a season's worth opens in the same spreadsheet as one night's.
    """
    if not _may_see(request.user):
        raise Http404

    replies = EventRsvp.objects.select_related('event', 'event__show')
    site_ids = _visible_site_ids(request.user)
    if site_ids is not None:
        replies = replies.filter(event__show__sites__pk__in=site_ids).distinct()
    event = None
    if request.GET.get('event'):
        event = get_object_or_404(Event.objects.select_related('show'),
                                  pk=request.GET['event'])
        replies = replies.filter(event=event)
    # By name within an event: on the night this is read by looking somebody up, not by
    # scanning it, and alphabetical is the only order that makes that quick.
    replies = replies.order_by('event__date', 'name')

    stem = f'rsvps-{slugify(event.name)}' if event else 'rsvps'
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{stem}.csv"'
    # Names and addresses: never let a shared cache or a crawler hold this.
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'

    writer = csv.writer(response)
    writer.writerow(['Event', 'Show', 'Date', 'Time', 'Response', 'Name', 'Email',
                     'Party size', 'Note', 'Replied', 'Last changed'])
    for r in replies:
        writer.writerow([
            _csv_safe(r.event.name), _csv_safe(r.event.show.name),
            r.event.date.isoformat(), r.event.time_range,
            r.get_response_display(), _csv_safe(r.name), _csv_safe(r.email),
            # A decline is forced back to one head, the same arithmetic the page shows.
            r.party_size if r.response != EventRsvp.NO else '',
            _csv_safe(r.note),
            r.created_at.date().isoformat(), r.updated_at.date().isoformat(),
        ])
    return response


@login_required
def visit_detail(request, pk):
    """Who a calendar entry actually is. Behind a login, which is the point of it.

    The calendar carries "Visit — 2 people" and a link here. That keeps names and email
    addresses out of every place a calendar entry ends up — a subscribed feed, a phone, a
    screen shared in a meeting — and puts them one authenticated click away instead.
    """
    if not _may_see(request.user):
        raise Http404
    visit = get_object_or_404(Visit.objects.select_related('site'), pk=pk)
    site_ids = _visible_site_ids(request.user)
    if site_ids is not None and visit.site_id not in site_ids:
        raise Http404
    tz = site_timezone(visit.site)
    with dj_timezone.override(tz):
        return render(request, 'gallery/visit_detail.html', {
            'visit': visit, 'site': visit.site, 'when': visit.when.astimezone(tz)})


@require_safe
def visits_ics(request, token):
    """The visits feed, found only by its secret.

    Unauthenticated because a subscribed calendar cannot sign in — Google fetches this with no
    cookies and no headers of ours — so the URL *is* the credential. It carries visitors' names
    and email addresses, which is why it is a random token rather than a slug, why it is never
    linked from a public page, and why it can be regenerated.

    A wrong token is a 404 rather than a 403: there is nothing to be gained by confirming that a
    visits feed exists at all.
    """
    site = Site.objects.filter(visit_feed_token=token).first() if token else None
    if site is None or not site.visits_enabled:
        raise Http404

    now = dj_timezone.now()
    visits = (Visit.objects
              .filter(site=site, cancelled_at__isnull=True,
                      when__gte=now - dt.timedelta(days=60))
              .order_by('when'))
    body = calendars.visits_feed(
        site, visits, domain=request.get_host().split(':')[0],
        url=request.build_absolute_uri(),
        base_url=request.build_absolute_uri('/'))
    response = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    # Inline, not an attachment: a Content-Disposition here makes most clients save a dead
    # snapshot instead of subscribing.
    response['Content-Disposition'] = 'inline; filename="visits.ics"'
    # Never let a shared cache hold a calendar of people's names and addresses.
    response['Cache-Control'] = 'private, max-age=300'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@login_required
@require_POST
def regenerate_visit_feed(request, pk):
    """Change the secret, which is the only way to deal with a URL that has got out."""
    if not is_staff_user(request.user):
        raise Http404
    site = get_object_or_404(Site, pk=pk)
    site.new_visit_feed_token()
    site.save(update_fields=['visit_feed_token'])
    messages.success(request, f'New calendar address for {site.name}. The old one has stopped '
                              f'working — subscribe again with the new one.')
    return redirect('gallery:visit_list')
