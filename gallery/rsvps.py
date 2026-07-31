"""Replying to an event, and being reminded about it.

The reminder is the reason this exists. One announcement three weeks out is a single shot at a
date nobody has planned around yet, and what goes wrong between that email and the night is
almost always forgetting rather than deciding not to come. A reminder to the whole mailing list
would be a second campaign; a reminder to somebody who said they were coming is a service they
asked for. The RSVP is what earns the right to send it.

Everything here reuses the visit-booking machinery deliberately: signed links instead of
accounts, a transactional send that never brings the request down with it, and add-to-calendar
rather than an invitation, because the gallery is not organising the reader's diary.
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

from gallery.calendars import site_timezone
from gallery.models import EventRsvp

logger = logging.getLogger(__name__)

RSVP_SALT = 'gallery.rsvps.change'

# How long before the event the reminder goes out. The evening before is when somebody who said
# maybe actually decides, and late enough that plans are real.
REMIND_DAYS_BEFORE = 1


def change_token(rsvp):
    return signing.dumps({'pk': rsvp.pk, 'email': rsvp.email}, salt=RSVP_SALT)


def rsvp_from_token(token):
    try:
        data = signing.loads(token, salt=RSVP_SALT)
    except signing.BadSignature:
        return None
    rsvp = (EventRsvp.objects
            .filter(pk=data.get('pk')).select_related('event__show').first())
    # The address is in the token as well as the id, so a recycled primary key cannot change
    # somebody else's answer.
    if rsvp and rsvp.email == data.get('email'):
        return rsvp
    return None


def absolute(path, request=None):
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, 'SITE_BASE_URL', 'https://www.120710.art').rstrip('/')
    return f'{base}{path}'


def change_url(rsvp, request=None):
    return absolute(reverse('event_rsvp_change',
                            kwargs={'token': change_token(rsvp)}), request)


def _event_site(event):
    return event.show.sites.first()


def _send(subject, to, template, context, tz=None):
    html = render_to_string(template, context)
    message = EmailMultiAlternatives(
        subject=subject, body=strip_tags(html),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[to])
    message.attach_alternative(html, 'text/html')
    if tz is not None:
        # Rendered in the venue's zone: TIME_ZONE is UTC, so without this every time in the
        # message is converted to UTC and the reader is told the wrong hour.
        from django.utils import timezone as tzmod
        with tzmod.override(tz):
            html = render_to_string(template, context)
            message.body = strip_tags(html)
            message.alternatives = [(html, 'text/html')]
    message.send()


def confirm(rsvp, request=None):
    """Their copy: what they said, when it is, and how to change their mind. Never raises."""
    event = rsvp.event
    site = _event_site(event)
    tz = site_timezone(site)
    try:
        _send(
            subject=f'{"See you at" if rsvp.is_coming else "Noted:"} {event.name}',
            to=rsvp.email, template='email/rsvp_confirm.html',
            context={
                'rsvp': rsvp, 'event': event, 'site': site,
                'change_url': change_url(rsvp, request),
                'event_url': absolute(event.get_absolute_url(), request),
                'ics_url': absolute(
                    reverse('gallery:event_ics', kwargs={'pk': event.pk}), request),
            },
            tz=tz)
    except Exception:   # noqa: BLE001 — the reply is recorded; a mail failure must not undo it
        logger.exception('Could not confirm RSVP %s to %s', rsvp.pk, rsvp.email)


def due_for_reminder(now=None):
    """RSVPs to remind: coming or undecided, event tomorrow, not already reminded.

    `reminded_at` is what makes this safe to run more than once a day — a cron that fires twice,
    or a deploy that re-runs it, must not send the same person the same reminder again.
    """
    now = now or dj_timezone.now()
    target = now.date() + dt.timedelta(days=REMIND_DAYS_BEFORE)
    return (EventRsvp.objects
            .filter(event__date=target,
                    response__in=EventRsvp.REMINDABLE,
                    reminded_at__isnull=True)
            .select_related('event__show'))


def send_reminder(rsvp, request=None):
    """One reminder. Returns True if it went. Marked before sending is not an option — see below."""
    event = rsvp.event
    site = _event_site(event)
    try:
        _send(
            subject=f'Tomorrow: {event.name}',
            to=rsvp.email, template='email/rsvp_reminder.html',
            context={
                'rsvp': rsvp, 'event': event, 'site': site,
                'change_url': change_url(rsvp, request),
                'event_url': absolute(event.get_absolute_url(), request),
                'maps_url': site.maps_url if site else '',
            },
            tz=site_timezone(site))
    except Exception:   # noqa: BLE001
        logger.exception('Could not remind %s about event %s', rsvp.email, event.pk)
        return False
    # Marked after, so a send that fails is retried on the next run. The opposite ordering
    # would lose the reminder silently, and a reminder that never arrives is the whole failure
    # this feature exists to prevent.
    rsvp.reminded_at = dj_timezone.now()
    rsvp.save(update_fields=['reminded_at'])
    return True
