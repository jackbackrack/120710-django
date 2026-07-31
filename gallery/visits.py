"""Offering visit slots, and telling the gallery about a booking.

Two halves, both deliberately small.

**Slots** are computed, never stored. A slot is a start time inside one of the venue's structured
opening blocks (`Site.open_periods_on`), far enough ahead to be useful, not past the booking
horizon, and not already full. Nothing is reserved and nothing expires, because slots are shared —
several visitors at the same half hour is fewer appointments for the gallery to keep, not a
conflict. That removes locking, held-slot cleanup and double-booking races in one go.

**Telling the gallery** is an email, not an API. A message carrying a `text/calendar` part with
`METHOD:REQUEST` *is* a calendar invitation: Google Calendar adds it to the owner's calendar
without being asked, and `METHOD:CANCEL` with the same `UID` takes it away again. No OAuth, no
scopes, no Google Cloud project, no refresh tokens, and nothing to go stale.

What this deliberately does **not** do is read the gallery's calendar to avoid clashes. That is
the expensive half — a secret feed to fetch and cache, or OAuth and a verified scope — and it buys
little here, because a slot that turns out to be inconvenient can simply be declined in the
invitation. Worth revisiting only if declining becomes the annoying part.
"""
import datetime as dt
import logging

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.utils.html import strip_tags

from gallery.calendars import _esc, _fold, _utc, site_timezone
from gallery.models import Visit

logger = logging.getLogger(__name__)

CANCEL_SALT = 'gallery.visits.cancel'


# ── Slots ────────────────────────────────────────────────────────────────────

def slots_for_day(site, day, now=None):
    """Bookable start times on one date, as aware datetimes in the venue's zone."""
    now = now or dj_timezone.now()
    tz = site_timezone(site)
    length = dt.timedelta(minutes=site.visit_slot_minutes or 30)
    earliest = now + dt.timedelta(hours=site.visit_lead_hours or 0)

    out = []
    for block in site.open_periods_on(day):
        cursor = dt.datetime.combine(day, block.start, tzinfo=tz)
        closes = dt.datetime.combine(day, block.end, tzinfo=tz)
        # A slot has to *finish* before closing time. Offering 5:45 for a half-hour visit at a
        # gallery that shuts at six is how somebody arrives to a locked door.
        while cursor + length <= closes:
            if cursor >= earliest:
                out.append(cursor)
            cursor += length
    return out


def taken(site, starts):
    """How many people are already booked at each of `starts`. One query, not one per slot."""
    from django.db.models import Sum
    if not starts:
        return {}
    rows = (Visit.objects
            .filter(site=site, cancelled_at__isnull=True, when__in=starts)
            .values('when').annotate(total=Sum('party_size')))
    return {row['when']: row['total'] or 0 for row in rows}


def available(site, now=None):
    """Every bookable slot in the horizon, grouped by date.

    Returns [(date, [(start, places_left_or_None), ...]), ...] with empty days dropped, which is
    what a booking page wants to render directly.
    """
    if not site.visits_enabled:
        return []
    now = now or dj_timezone.now()
    tz = site_timezone(site)
    today = now.astimezone(tz).date()
    horizon = site.visit_horizon_days or 30

    days = [today + dt.timedelta(days=offset) for offset in range(horizon + 1)]
    every = {day: slots_for_day(site, day, now=now) for day in days}
    counts = taken(site, [start for starts in every.values() for start in starts])

    cap = site.visit_capacity or 0
    out = []
    for day in days:
        row = []
        for start in every[day]:
            if not cap:
                row.append((start, None))
                continue
            left = cap - counts.get(start, 0)
            if left > 0:
                row.append((start, left))
        if row:
            out.append((day, row))
    return out


def is_bookable(site, when, party_size=1, now=None):
    """Whether this exact slot can still take this many people.

    Re-checked at submission rather than trusted from the form: the page may have been open for
    an hour, and the lead time alone will have moved on.
    """
    tz = site_timezone(site)
    day = when.astimezone(tz).date()
    if when not in slots_for_day(site, day, now=now):
        return False
    cap = site.visit_capacity or 0
    if not cap:
        return True
    return taken(site, [when]).get(when, 0) + party_size <= cap


# ── Telling the gallery ──────────────────────────────────────────────────────

def cancel_token(visit):
    return signing.dumps({'pk': visit.pk, 'email': visit.email}, salt=CANCEL_SALT)


def visit_from_token(token, max_age=None):
    try:
        data = signing.loads(token, salt=CANCEL_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    visit = Visit.objects.filter(pk=data.get('pk')).select_related('site').first()
    # The address is in the token as well as the id, so a recycled primary key cannot cancel
    # somebody else's booking.
    if visit and visit.email == data.get('email'):
        return visit
    return None


def _absolute(path, request=None):
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, 'SITE_BASE_URL', 'https://www.120710.art').rstrip('/')
    return f'{base}{path}'


def invitation(visit, method='REQUEST', domain='120710.art', request=None):
    """A VCALENDAR carrying one visit, as an invitation or a cancellation.

    `METHOD:REQUEST` is what makes a mail client treat this as an invitation rather than a file to
    download, which is the whole trick — Google Calendar adds it by itself. `METHOD:CANCEL` with
    the same UID and a higher SEQUENCE removes it again.

    The gallery is the ATTENDEE and the venue address is the ORGANIZER, which is back to front
    from a person's point of view but right from the calendar's: the invitation is *to* the
    gallery owner, and it has to come from an address they trust or Google will not add it.
    """
    site = visit.site
    tz = site_timezone(site)
    now = dj_timezone.now()
    organiser = site.email or settings.DEFAULT_FROM_EMAIL
    location = ', '.join(filter(None, [site.name,
                                       site.formatted_address.replace('\n', ', ')]))
    summary = f'Gallery visit — {visit.name}'
    details = [f'{visit.party_size} '
               f'{"person" if visit.party_size == 1 else "people"}',
               f'Booked by {visit.name} <{visit.email}>']
    if visit.note:
        details.append(visit.note)
    details.append(_absolute(
        reverse('visit_cancel', kwargs={'token': cancel_token(visit)}), request))

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:-//{domain}//Gallery visits//EN',
        'CALSCALE:GREGORIAN',
        f'METHOD:{method}',
        'BEGIN:VEVENT',
        f'UID:{visit.uid(domain)}',
        f'SEQUENCE:{visit.sequence}',
        f'DTSTAMP:{_utc(now)}',
        f'DTSTART:{_utc(visit.when.astimezone(tz))}',
        f'DTEND:{_utc(visit.ends.astimezone(tz))}',
        f'SUMMARY:{_esc(summary)}',
        f'DESCRIPTION:{_esc(chr(10).join(details))}',
        f'LOCATION:{_esc(location)}',
        f'ORGANIZER;CN={_esc(site.name)}:mailto:{organiser}',
        f'ATTENDEE;CN={_esc(visit.name)};RSVP=FALSE:mailto:{visit.email}',
        'STATUS:CANCELLED' if method == 'CANCEL' else 'STATUS:CONFIRMED',
        'END:VEVENT',
        'END:VCALENDAR',
    ]
    folded = []
    for line in lines:
        folded.extend(_fold(line))
    return '\r\n'.join(folded) + '\r\n'


def _send(subject, to, template, context, ics=None, method='REQUEST', tz=None):
    # Rendered in the venue's zone. TIME_ZONE is UTC, so without this Django converts every time
    # in the message to UTC and the visitor is told to arrive seven hours late.
    if tz is not None:
        with dj_timezone.override(tz):
            html = render_to_string(template, context)
    else:
        html = render_to_string(template, context)
    message = EmailMultiAlternatives(
        subject=subject, body=strip_tags(html),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[to])
    message.attach_alternative(html, 'text/html')
    if ics:
        # As an alternative part, not an attachment: that is what makes Google Calendar treat it
        # as an invitation to act on rather than a file sitting at the bottom of the message.
        message.attach_alternative(ics, f'text/calendar; method={method}; charset=UTF-8')
    message.send()


def notify_gallery(visit, method='REQUEST', request=None):
    """Send the gallery the invitation, or its cancellation. Never raises.

    A booking that is confirmed to the visitor but never reaches the gallery is the bad outcome,
    so this is logged loudly — but it must not undo the booking either, because the visitor has
    already been told it worked.
    """
    site = visit.site
    to = site.email or settings.DEFAULT_FROM_EMAIL
    verb = 'cancelled' if method == 'CANCEL' else 'booked'
    try:
        _send(
            subject=f'Visit {verb}: {visit.name}, '
                    f'{visit.when.astimezone(site_timezone(site)):%a %-d %b %-I:%M %p}',
            to=to, template='email/visit_gallery.html',
            context={'visit': visit, 'site': site, 'cancelled': method == 'CANCEL',
                     'when': visit.when.astimezone(site_timezone(site))},
            ics=invitation(visit, method=method, request=request), method=method,
            tz=site_timezone(site))
    except Exception:   # noqa: BLE001 — the visitor has already been told this worked
        logger.exception('Could not send the gallery a %s for visit %s', method, visit.pk)


def confirm_to_visitor(visit, request=None):
    """Their copy, with the details and a way out. Never raises, for the same reason."""
    site = visit.site
    try:
        _send(
            subject=f'Your visit to {site.name}',
            to=visit.email, template='email/visit_visitor.html',
            context={
                'visit': visit, 'site': site,
                'when': visit.when.astimezone(site_timezone(site)),
                'cancel_url': _absolute(
                    reverse('visit_cancel', kwargs={'token': cancel_token(visit)}), request),
                'maps_url': site.maps_url,
            },
            ics=invitation(visit, method='PUBLISH', request=request), method='PUBLISH',
            tz=site_timezone(site))
    except Exception:   # noqa: BLE001
        logger.exception('Could not confirm visit %s to %s', visit.pk, visit.email)
