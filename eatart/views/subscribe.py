"""Joining a mailing list, from the web form or from a kiosk at an opening.

Both are single opt-in: submitting the form subscribes you, with no link to click first. That is
a deliberate choice over double opt-in, which would have cost a real share of signups to guard
against a risk that is small at this scale — a handful of form submissions a week, against an
imported list of a thousand addresses that is the actual deliverability exposure.

What replaces it is a welcome email sent immediately, which buys most of the same protection
without the friction:

  * A dead address bounces on that one message, and the bounce webhook removes it **before** it
    ever receives a campaign. A typo costs one bounce instead of bouncing on every mailing.
  * Anybody added by somebody else gets a prominent one-click unsubscribe straight away, rather
    than finding out months later.
  * It is an engaged send, which is the useful kind of volume on a young sending domain.

The welcome email is transactional, so it goes through the normal backend rather than the
campaign provider — see gallery/campaigns.py::_connection for why those are kept apart.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import Http404
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from honeypot.decorators import check_honeypot

from eatart.forms.subscribe import KioskSubscribeForm, SubscribeForm
from gallery.models import Subscriber, Subscription

logger = logging.getLogger(__name__)


def _already_on(email, site):
    """Whether this address is already an active member of this list.

    Checked before subscribing so the welcome email goes only to somebody actually joining.
    Re-sending it every time a form is submitted would make the form a way to pester someone.
    """
    return Subscription.objects.filter(
        subscriber__email=(email or '').strip().lower(), site=site,
        is_subscribed=True).exists()


def send_welcome(request, subscription):
    """Greet a new subscriber, and give the address a chance to fail early.

    Never allowed to break the signup: they are already subscribed by the time this runs, so a
    mail failure here is worth logging and nothing else. Refusing to subscribe somebody because
    our own mail server was briefly unhappy would be the worse outcome.
    """
    from gallery.campaigns import privacy_url, unsubscribe_url

    site = subscription.site
    try:
        html = render_to_string('email/subscribe_welcome.html', {
            'first_name': subscription.subscriber.first_name,
            'list_name': site.name if site else 'reset.art',
            'unsubscribe_url': unsubscribe_url(subscription, request),
            'privacy_url': privacy_url(site, request),
        }, request=request)
        message = EmailMultiAlternatives(
            subject=f'Welcome to the {site.name if site else "reset.art"} mailing list',
            body=strip_tags(html),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[subscription.subscriber.email],
        )
        message.attach_alternative(html, 'text/html')
        # RFC 8058, same as a campaign. This is list mail, so the one-click button Gmail shows
        # ought to work on it — and this is the message most likely to reach somebody who never
        # asked to be on the list.
        url = unsubscribe_url(subscription, request)
        message.extra_headers['List-Unsubscribe'] = f'<{url}>'
        message.extra_headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        message.send()
    except Exception:   # noqa: BLE001 — logged; the subscription stands regardless
        logger.exception('Could not send welcome email to %s',
                         subscription.subscriber.email)


@check_honeypot()
def subscribe(request):
    if request.method == 'POST':
        form = SubscribeForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            site = Subscriber.default_site()
            new = not _already_on(email, site)
            # Straight into our own table. There is no third-party call to fail here any
            # more, which is the point: the list is ours, and a provider outage cannot lose
            # a subscriber between the form and the send.
            _, subscriptions = Subscriber.opt_in(
                email=email,
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'],
                sites=[site],
                source=Subscription.SOURCE_SUBSCRIBE_FORM,
            )
            if new and subscriptions:
                send_welcome(request, subscriptions[0])
            messages.success(request, f'Successfully subscribed {email}!')
            return redirect(request.path)
    else:
        form = SubscribeForm()

    return render(request, 'subscribe/form.html', {'form': form})


@check_honeypot()
def subscribe_kiosk(request, token):
    kiosk_token = settings.KIOSK_TOKEN
    if not kiosk_token or token != kiosk_token:
        raise Http404
    success = failure = None
    if request.method == 'POST':
        form = KioskSubscribeForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            site = Subscriber.default_site()
            new = not _already_on(email, site)
            _, subscriptions = Subscriber.opt_in(
                email=email,
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'],
                sites=[site],
                source=Subscription.SOURCE_KIOSK,
            )
            if new and subscriptions:
                # Worth it here too: an address mistyped on a tablet at an opening is exactly
                # the kind that would otherwise bounce on every mailing for years.
                send_welcome(request, subscriptions[0])
            success = f'Thanks! {email} has been subscribed.'
            form = KioskSubscribeForm()
    else:
        form = KioskSubscribeForm()

    return render(request, 'subscribe/kiosk.html', {
        'form': form,
        'success': success,
        'failure': failure,
    })
