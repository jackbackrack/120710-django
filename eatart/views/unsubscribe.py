"""Unsubscribe: the human page, and the one-click endpoint mail clients call.

Two behaviours behind one URL, because RFC 8058 requires it:

  GET   a page with a button. A person clicked the link in the footer, and a link that
        unsubscribes on GET gets triggered by link-scanning security appliances and mail
        previewers, silently unsubscribing people who never asked.

  POST  unsubscribes immediately with no confirmation. This is what Gmail's and Yahoo's
        "Unsubscribe" button calls, driven by the List-Unsubscribe-Post header. It must work
        without a session, without CSRF, and without a redirect, or the client reports the
        unsubscribe as failed and the recipient reaches for the spam button instead.
"""
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from gallery.campaigns import subscription_from_token
from gallery.models import Subscription


# csrf_exempt because the POST comes from Gmail's infrastructure, which has no CSRF token
# and never will. The signed token in the URL is the authentication: it cannot be guessed
# or enumerated, and it names exactly one subscriber.
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def unsubscribe(request, token):
    subscription = subscription_from_token(token)

    if request.method == 'POST':
        # One-click, and it defaults to this list only — that is what the header promised.
        # "Everything" is an explicit choice on the page, because a mail client's button
        # should not silently unsubscribe someone from galleries they never complained
        # about. A bad token still answers 200: telling a mail client the unsubscribe
        # failed makes it warn the recipient, and there is nothing they could do about it.
        everything = request.POST.get('scope') == 'all'
        if subscription:
            if everything:
                subscription.subscriber.unsubscribe_all()
            else:
                subscription.unsubscribe(reason=Subscription.UNSUB_REQUESTED)

        # Two very different callers reach this line. RFC 8058 says a mail client's
        # one-click POST carries exactly `List-Unsubscribe=One-Click` in the body; it
        # wants a 200 and nothing else, and nobody reads what it returns. Anyone else
        # posting here is a person who pressed the button on our own page, and they were
        # being handed a bare text/plain "Unsubscribed" — no page, no acknowledgement,
        # not even the site around it.
        if request.POST.get('List-Unsubscribe') == 'One-Click':
            return HttpResponse('Unsubscribed', content_type='text/plain')

        return render(request, 'public/unsubscribe.html', {
            'done': True,
            'everything': everything,
            'subscription': subscription,
            'subscriber': subscription.subscriber if subscription else None,
            'invalid': subscription is None,
        })

    others = []
    if subscription:
        others = [s for s in subscription.subscriber.subscriptions.filter(is_subscribed=True)
                  if s.pk != subscription.pk]
    return render(request, 'public/unsubscribe.html', {
        'subscription': subscription,
        'subscriber': subscription.subscriber if subscription else None,
        'others': others,
        'token': token,
        # A link that has already been used should say so rather than look broken.
        'already': bool(subscription and not subscription.is_subscribed),
        'invalid': subscription is None,
    })
