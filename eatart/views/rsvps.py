"""Replying to an event, and changing that reply.

Public, no account. Same reasoning as visit booking: an RSVP is a warm invitation being accepted,
and putting a signup in front of it costs more replies than the spam it prevents. Protected by
the honeypot and reCAPTCHA the other public forms use.
"""
import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from honeypot.decorators import check_honeypot

from eatart.forms.rsvps import RsvpForm
from gallery import rsvps as engine
from gallery.models import Event, EventRsvp, Show

logger = logging.getLogger(__name__)


def _public_event(pk):
    event = get_object_or_404(Event.objects.select_related('show'), pk=pk)
    if event.show.status not in Show.PUBLIC_STATUSES:
        raise Http404
    return event


@check_honeypot()
@require_POST
def event_rsvp(request, pk):
    event = _public_event(pk)
    if event.is_past:
        messages.error(request, 'That event has already happened.')
        return redirect(event.get_absolute_url())

    form = RsvpForm(request.POST, user=request.user)
    if not form.is_valid():
        # Re-rendered on the event page rather than a page of its own, so nothing they typed is
        # lost and the event stays in front of them.
        return render(request, 'gallery/event_detail.html',
                      {'event': event, 'rsvp_form': form}, status=400)

    email = form.cleaned_data['email'].strip().lower()
    # Updated rather than added: a second reply is somebody changing their mind, not a second
    # guest, and two rows would inflate what the gallery caters for.
    rsvp, created = EventRsvp.objects.update_or_create(
        event=event, email=email,
        defaults={
            'name': form.cleaned_data['name'],
            'response': form.cleaned_data['response'],
            'party_size': form.cleaned_data['party_size'],
            'note': form.cleaned_data['note'],
            # A changed answer earns a fresh reminder: somebody who switches from no to yes the
            # week before should still be reminded the night before.
            'reminded_at': None,
        })
    engine.confirm(rsvp, request=request)
    logger.info('RSVP %s for event %s: %s (%s)', rsvp.pk, event.pk, rsvp.response,
                'new' if created else 'changed')

    return render(request, 'rsvps/thanks.html', {'rsvp': rsvp, 'event': event})


def event_rsvp_change(request, token):
    """The link in their confirmation. Shows what they said, and lets them say something else."""
    rsvp = engine.rsvp_from_token(token)
    if rsvp is None:
        return render(request, 'rsvps/thanks.html', {'invalid': True}, status=400)

    if request.method == 'POST':
        response = request.POST.get('response')
        if response in dict(EventRsvp.RESPONSE_CHOICES):
            rsvp.response = response
            # Fresh answer, fresh reminder — see event_rsvp.
            rsvp.reminded_at = None
            rsvp.save(update_fields=['response', 'reminded_at', 'updated_at'])
            logger.info('RSVP %s changed to %s', rsvp.pk, response)
            return render(request, 'rsvps/thanks.html',
                          {'rsvp': rsvp, 'event': rsvp.event, 'changed': True})

    return render(request, 'rsvps/change.html', {'rsvp': rsvp, 'event': rsvp.event})
