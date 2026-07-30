"""Act on delivery events Resend sends back, so a dead or hostile address stops
receiving mail without anyone watching a dashboard.

Only two events change anything. A **hard bounce** means the address does not work; a
**complaint** means the person pressed "this is spam". Everything else Resend reports —
delivered, opened, clicked, deferred — is noise for our purposes and is ignored rather
than stored, because we have no use for it that would justify keeping behavioural data
about subscribers.

Both stop mail on *every* list the person is on, not just the campaign's list. That is
deliberately stricter than the unsubscribe link, which narrows to one list on the
grounds that a mail client's button should not cancel galleries the reader never named.
These signals are different in kind: a bounce says no list can reach them, and a
complaint is someone reporting us — continuing to mail them from a sibling gallery is
how a sending domain gets blocked.

Anymail verifies the Svix signature before this runs (ANYMAIL['RESEND_SIGNING_SECRET']),
so an unsigned or tampered POST never reaches here.
"""
import logging

from anymail.signals import EventType, tracking
from django.dispatch import receiver

from gallery.models import Subscriber, Subscription

logger = logging.getLogger(__name__)

# Which reason we record, keyed by what the provider told us. Kept apart because
# "stopped working" and "reported us" want different answers if we ever reconsider an
# address, and collapsing them would throw that away.
_STOP_SENDING = {
    EventType.BOUNCED: Subscription.UNSUB_BOUNCED,
    EventType.COMPLAINED: Subscription.UNSUB_COMPLAINED,
}


@receiver(tracking)
def handle_tracking_event(sender, event, esp_name, **kwargs):
    reason = _STOP_SENDING.get(event.event_type)
    if reason is None:
        return

    address = (event.recipient or '').strip().lower()
    if not address:
        logger.warning('%s %s event with no recipient', esp_name, event.event_type)
        return

    subscriber = Subscriber.objects.filter(email__iexact=address).first()
    if subscriber is None:
        # Campaigns are the only mail sent through Resend, so this should not happen.
        # Worth a line if it does: it means something is sending to addresses that are
        # not on our list.
        logger.warning('%s %s for %s, who is not a subscriber',
                       esp_name, event.event_type, address)
        return

    stopped = subscriber.unsubscribe_all(reason=reason)
    logger.info('%s %s for %s — stopped %d subscription(s)',
                esp_name, event.event_type, address, stopped)
