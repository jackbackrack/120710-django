"""Booking a visit, and cancelling one.

Public, no account needed. Protected by the honeypot and reCAPTCHA the other public forms use
rather than by a confirmation email: a booking is not a mailing-list signup, and since slots are
shared a spammed booking wastes nobody's slot — it is noise in a calendar, not a denied visitor.
Making every genuine visitor click a second link to prevent it would cost more than it saves.
"""
import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import dateparse, timezone as dj_timezone

from honeypot.decorators import check_honeypot

from eatart.forms.visits import VisitForm
from gallery import visits as engine
from gallery.calendars import site_timezone
from gallery.models import Subscriber, Visit

logger = logging.getLogger(__name__)


def _venue(site_slug=None):
    """The venue being booked: the one named in the URL, or the deployment's own."""
    from gallery.models import Site

    if site_slug:
        site = Site.objects.filter(slug=site_slug).first()
    else:
        site = Subscriber.default_site()
    if site is None or not site.visits_enabled:
        raise Http404
    return site


@check_honeypot()
def book_visit(request, site_slug=None):
    site = _venue(site_slug)
    tz = site_timezone(site)

    if request.method == 'POST':
        form = VisitForm(request.POST)
        if form.is_valid():
            when = form.cleaned_data['when']
            party = form.cleaned_data['party_size']
            # Re-checked here, not trusted from the form: the page may have been open for an
            # hour, and the notice period alone will have moved on since it was rendered.
            if not engine.is_bookable(site, when, party_size=party):
                messages.error(request, 'Sorry — that time has just gone. Please pick another.')
                return redirect(request.path)

            visit = Visit.objects.create(
                site=site, when=when, minutes=site.visit_slot_minutes or 30,
                name=form.cleaned_data['name'], email=form.cleaned_data['email'],
                party_size=party, note=form.cleaned_data['note'])
            engine.confirm_to_visitor(visit, request=request)
            engine.notify_gallery(visit, request=request)
            logger.info('Visit %s booked at %s for %s', visit.pk, site.slug, visit.when)
            return render(request, 'visits/booked.html', {
                'site': site, 'visit': visit, 'when': visit.when.astimezone(tz)})
    else:
        form = VisitForm()

    return render(request, 'visits/book.html', {
        'site': site,
        'form': form,
        'days': engine.available(site),
        'timezone': tz.key,
    })


def cancel_visit(request, token):
    """One click from the confirmation email. No sign-in, no reason asked for.

    A GET shows what is about to be cancelled and a POST does it, because a mail client that
    prefetches links must not be able to cancel somebody's visit on their behalf.
    """
    visit = engine.visit_from_token(token)
    if visit is None:
        return render(request, 'visits/cancelled.html', {'invalid': True}, status=400)

    tz = site_timezone(visit.site)
    if request.method == 'POST' and not visit.is_cancelled:
        visit.cancelled_at = dj_timezone.now()
        # Advanced before the cancellation goes out: a calendar client ignores an update whose
        # SEQUENCE has not moved, so a cancellation at the same sequence would be dropped and
        # the appointment would sit in the gallery's calendar for good.
        visit.sequence += 1
        visit.save(update_fields=['cancelled_at', 'sequence'])
        engine.notify_gallery(visit, method='CANCEL', request=request)
        logger.info('Visit %s cancelled by the visitor', visit.pk)

    return render(request, 'visits/cancelled.html', {
        'visit': visit, 'site': visit.site, 'when': visit.when.astimezone(tz),
        'done': visit.is_cancelled,
    })
