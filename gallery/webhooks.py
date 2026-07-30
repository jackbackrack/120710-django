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
import datetime
import logging

from anymail.signals import EventType, tracking
from django.dispatch import receiver
from django.utils import timezone

from gallery.models import CampaignDelivery, Subscriber, Subscription

logger = logging.getLogger(__name__)

# Which reason we record, keyed by what the provider told us. Kept apart because
# "stopped working" and "reported us" want different answers if we ever reconsider an
# address, and collapsing them would throw that away.
_STOP_SENDING = {
    EventType.BOUNCED: Subscription.UNSUB_BOUNCED,
    EventType.COMPLAINED: Subscription.UNSUB_COMPLAINED,
}

# The same two events, as outcomes recorded against the campaign that caused them.
_OUTCOME = {
    EventType.BOUNCED: CampaignDelivery.OUTCOME_BOUNCED,
    EventType.COMPLAINED: CampaignDelivery.OUTCOME_COMPLAINED,
}

# How far back to look for the campaign an event belongs to. A bounce follows its send within
# minutes, so the most recent delivery is the cause. A complaint does not: people press the spam
# button on months-old mail, and attributing that to whatever went out last week would blame a
# campaign that had nothing to do with it and inflate its complaint rate. Past this, the person is
# still unsubscribed — the event is simply not counted against any campaign.
ATTRIBUTION_WINDOW = datetime.timedelta(days=30)


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
    recorded = _attribute(subscriber, event.event_type)
    logger.info('%s %s for %s — stopped %d subscription(s)%s',
                esp_name, event.event_type, address, stopped,
                f', recorded against campaign {recorded}' if recorded else '')


def _attribute(subscriber, event_type):
    """Record the event against the campaign that caused it. Returns the campaign id, or None.

    Without this the pages can only report what we did — how many a campaign went to — and never
    what happened next, which is the half that matters: bounce rate is how a stale imported list
    announces itself, and complaint rate is the number Gmail and Yahoo actually judge a sender on.

    The most recent delivery is the cause, because a bounce arrives within minutes of its send.
    Only the first event counts: a provider that retries a webhook must not be able to count one
    complaint twice and double a campaign's rate.
    """
    delivery = (CampaignDelivery.objects
                .filter(subscription__subscriber=subscriber,
                        status=CampaignDelivery.STATUS_SENT,
                        outcome='',
                        sent_at__gte=timezone.now() - ATTRIBUTION_WINDOW)
                .order_by('-sent_at').first())
    if delivery is None:
        return None
    delivery.outcome = _OUTCOME[event_type]
    delivery.outcome_at = timezone.now()
    delivery.save(update_fields=['outcome', 'outcome_at'])
    return delivery.campaign_id
