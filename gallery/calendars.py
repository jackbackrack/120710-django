"""One timeline of shows and events, and the iCalendar feed built from it.

Shows are date *ranges* weeks long; events are single days with times. A visitor's question
spans both — "what is on, and what is happening?" — and until now nothing merged them: shows
were grouped into current/future/past on one page and events listed on another.

The feed is hand-rolled rather than pulling in a library, following
`ArtistSchedule.ics()`, which already gets the fiddly parts right: CRLF line endings,
escaping, stable UIDs, and METHOD:PUBLISH.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone

from gallery.models import Event, Show

# A show is an all-day span; an event is a slot on one day. Kept as an explicit kind rather
# than duck-typing in the template, because the two render differently and always will.
KIND_SHOW = 'show'
KIND_EVENT = 'event'

DEFAULT_TIMEZONE = 'America/Los_Angeles'


class Entry:
    """One row of the agenda. `sort_date` is what orders the timeline."""

    __slots__ = ('kind', 'obj', 'sort_date', 'end_date')

    def __init__(self, kind, obj, sort_date, end_date=None):
        self.kind = kind
        self.obj = obj
        self.sort_date = sort_date
        self.end_date = end_date or sort_date

    @property
    def is_show(self):
        return self.kind == KIND_SHOW

    @property
    def name(self):
        return self.obj.name

    @property
    def url(self):
        return self.obj.get_absolute_url()

    @property
    def spans_days(self):
        return self.end_date > self.sort_date

    @property
    def show_start(self):
        """The run this entry belongs to — its own, for a show; its show's, for an event.

        Lets the past be ordered by show while keeping each show above its own events. Plain
        descending order splits them: an opening on the 3rd sorts above the show that opened
        on the 2nd, so the event appears above the thing it is part of.
        """
        return self.sort_date if self.is_show else self.obj.show.start or self.sort_date

    def __repr__(self):                                  # pragma: no cover — debugging aid
        return f'<Entry {self.kind} {self.name!r} {self.sort_date}>'


def site_timezone(site):
    """The venue's zone, falling back to the gallery's own rather than to UTC.

    UTC would be the wrong default: no venue keeps UTC opening hours, and silently
    publishing 6pm as 18:00Z shifts every event by the offset.
    """
    name = getattr(site, 'timezone', '') or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:                                    # an invalid name saved past validation
        return ZoneInfo(DEFAULT_TIMEZONE)


def timeline(site=None, user=None, include_past=True):
    """Shows and events on one date-ordered timeline, newest last.

    Scoped to a venue when given, matching the listing views. Only publicly visible shows
    are included — the feed is public, and an unannounced show must not leak through it.
    """
    from django.contrib.auth.models import AnonymousUser
    from gallery.permissions import visible_show_queryset

    # None means "whatever the public may see" — the feed passes it so the response never
    # depends on who is signed in, since the URL is cacheable and shared.
    if user is None:
        user = AnonymousUser()
    shows = visible_show_queryset(
        Show.objects.prefetch_related('sites', 'events'), user)
    if site is not None:
        shows = shows.filter(sites=site).distinct()

    entries = []
    show_ids = []
    for show in shows:
        if show.start is None:
            continue                                     # undated: nothing to place it on
        show_ids.append(show.pk)
        entries.append(Entry(KIND_SHOW, show, show.start, show.end or show.start))

    # Events hang off shows, so a scoped or invisible show's events go with it.
    events = (Event.objects.filter(show_id__in=show_ids)
              .select_related('show')
              .prefetch_related('show__sites'))
    for event in events:
        entries.append(Entry(KIND_EVENT, event, event.date))

    if not include_past:
        today = dt.date.today()
        entries = [e for e in entries if e.end_date >= today]

    # Shows before their own events on a shared date: a show opening and its opening party
    # read in that order.
    entries.sort(key=lambda e: (e.sort_date, e.kind != KIND_SHOW, e.name))
    return entries


def archive_order(entries):
    """Past entries, most recent show first, each show above its own events.

    Not a plain reverse of the timeline. Browsing history is browsing *shows* — the archive
    is a list of what has hung here — so the show leads and its events follow it, even though
    an event's own date is later.
    """
    return sorted(
        entries,
        key=lambda e: (e.show_start, e.is_show, e.sort_date),
        reverse=True,
    )


def group_by_month(entries):
    """[(date-of-first-of-month, [entries]), ...] for a month-headed agenda."""
    grouped = {}
    for entry in entries:
        key = entry.sort_date.replace(day=1)
        grouped.setdefault(key, []).append(entry)
    return sorted(grouped.items())


# ── The feed ─────────────────────────────────────────────────────────────────

def _esc(value):
    """iCalendar text escaping. Backslash first, or it double-escapes the others."""
    return (str(value or '')
            .replace('\\', '\\\\')
            .replace(';', '\\;')
            .replace(',', '\\,')
            .replace('\n', '\\n'))


def _fold(line):
    """Fold to 75 octets per RFC 5545, continuations starting with one space.

    Not cosmetic: a long SUMMARY on an unfolded line is what makes some clients drop the
    whole event rather than just truncate it.
    """
    encoded = line.encode('utf-8')
    if len(encoded) <= 75:
        return [line]
    out, current = [], ''
    for char in line:
        candidate = current + char
        limit = 75 if not out else 74          # continuations spend one octet on the space
        if len(candidate.encode('utf-8')) > limit:
            out.append(current if not out else ' ' + current)
            current = char
        else:
            current = candidate
    if current:
        out.append(current if not out else ' ' + current)
    return out


def _utc(stamp):
    return stamp.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _event_lines(entry, tz, domain, now):
    obj = entry.obj
    if entry.is_show:
        # All-day, and DTEND is *exclusive* — a show ending 31 Aug must say 1 Sep, or every
        # client draws it a day short. The single most common iCalendar bug.
        start = obj.start.strftime('%Y%m%d')
        end = (entry.end_date + dt.timedelta(days=1)).strftime('%Y%m%d')
        lines = [
            f'UID:show-{obj.pk}@{domain}',
            f'DTSTART;VALUE=DATE:{start}',
            f'DTEND;VALUE=DATE:{end}',
            f'SUMMARY:{_esc(obj.name)}',
        ]
    else:
        # Converted to a UTC instant using the venue's zone. No VTIMEZONE block needed, and
        # a subscriber in New York sees a 6pm Berkeley opening at 9pm, which is correct.
        start = dt.datetime.combine(obj.date, obj.start, tzinfo=tz)
        end = dt.datetime.combine(obj.date, obj.end, tzinfo=tz)
        if end <= start:
            end = start + dt.timedelta(hours=2)          # an end before its start: guess
        lines = [
            f'UID:event-{obj.pk}@{domain}',
            f'DTSTART:{_utc(start)}',
            f'DTEND:{_utc(end)}',
            f'SUMMARY:{_esc(obj.name)} — {_esc(obj.show.name)}',
        ]

    description = getattr(obj, 'description', '') or ''
    if description:
        lines.append(f'DESCRIPTION:{_esc(description)}')

    sites = list(obj.sites.all()) if entry.is_show else list(obj.show.sites.all())
    if sites:
        location = ', '.join(
            filter(None, [sites[0].name, sites[0].formatted_address.replace('\n', ', ')]))
        lines.append(f'LOCATION:{_esc(location)}')

    lines.append(f'DTSTAMP:{_utc(now)}')
    return ['BEGIN:VEVENT'] + lines + ['END:VEVENT']


def feed(entries, name, domain, site=None, url=None):
    """A VCALENDAR document for `entries`."""
    tz = site_timezone(site)
    now = dj_timezone.now()
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:-//{domain}//Shows and Events//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        # Non-standard but universally honoured. Without it Apple Calendar names the
        # subscription after its URL.
        f'X-WR-CALNAME:{_esc(name)}',
        f'X-WR-TIMEZONE:{tz.key}',
    ]
    if url:
        lines.append(f'X-ORIGINAL-URL:{_esc(url)}')
    for entry in entries:
        lines.extend(_event_lines(entry, tz, domain, now))
    lines.append('END:VCALENDAR')

    folded = []
    for line in lines:
        folded.extend(_fold(line))
    return '\r\n'.join(folded) + '\r\n'


def last_modified(entries):
    """The newest created_at across the entries, for a Last-Modified header.

    Calendar clients poll on their own schedule and ignore requests to do otherwise, so the
    cheapest thing is to let them 304.
    """
    stamps = [getattr(e.obj, 'created_at', None) for e in entries]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def visit_summary(visit):
    """What a calendar entry says: that there is one, and how many people.

    Deliberately no name. A calendar entry ends up in more places than an inbox does — a
    subscribed feed, a phone on a table, a screen shared in a meeting — and the gallery does not
    need to know who is coming in order to know it is booked. Who they are is one authenticated
    click away instead.
    """
    people = f'{visit.party_size} {"person" if visit.party_size == 1 else "people"}'
    return f'Visit — {people}'


def visit_description(visit, base_url=''):
    from django.urls import reverse

    lines = []
    if visit.note:
        lines.append(visit.note)
    if visit.site.arrival_note:
        lines.append(visit.site.arrival_note)
    if base_url:
        lines.append(f'Who this is: {base_url.rstrip("/")}'
                     f'{reverse("gallery:visit_detail", kwargs={"pk": visit.pk})}')
    return '\n'.join(lines)


def visits_feed(site, visits, domain, url=None, base_url=''):
    """A VCALENDAR of booked visits, for the gallery to subscribe to.

    Complements the invitation emails rather than replacing them. Google refreshes a subscribed
    external calendar on its own schedule — commonly hours, and it ignores any refresh hint in
    the file — so this is the standing overview, and the invitation is what tells you about a
    booking made this morning.

    Cancelled visits are simply absent. On the next poll they disappear, which is the whole
    behaviour a subscribed feed offers and the reason the SEQUENCE dance is not needed here.
    """
    tz = site_timezone(site)
    now = dj_timezone.now()
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:-//{domain}//Gallery visits//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_esc(f"{site.name} — visits")}',
        f'X-WR-TIMEZONE:{tz.key}',
    ]
    if url:
        lines.append(f'X-ORIGINAL-URL:{_esc(url)}')

    for visit in visits:
        lines.extend([
            'BEGIN:VEVENT',
            f'UID:{visit.uid(domain)}',
            f'DTSTAMP:{_utc(now)}',
            f'DTSTART:{_utc(visit.when.astimezone(tz))}',
            f'DTEND:{_utc(visit.ends.astimezone(tz))}',
            f'SUMMARY:{_esc(visit_summary(visit))}',
            f'DESCRIPTION:{_esc(visit_description(visit, base_url))}',
            f'LOCATION:{_esc(", ".join(filter(None, [site.name, site.formatted_address.replace(chr(10), ", ")])))}',
            'END:VEVENT',
        ])
    lines.append('END:VCALENDAR')

    folded = []
    for line in lines:
        folded.extend(_fold(line))
    return '\r\n'.join(folded) + '\r\n'
