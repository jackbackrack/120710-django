from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from honeypot.decorators import check_honeypot

from eatart.forms.subscribe import KioskSubscribeForm, SubscribeForm
from gallery.models import Subscriber, Subscription


@check_honeypot()
def subscribe(request):
    if request.method == 'POST':
        form = SubscribeForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            # Straight into our own table. There is no third-party call to fail here any
            # more, which is the point: the list is ours, and a provider outage cannot lose
            # a subscriber between the form and the send.
            Subscriber.opt_in(
                email=email,
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'],
                sites=[Subscriber.default_site()],
                source=Subscription.SOURCE_SUBSCRIBE_FORM,
            )
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
            Subscriber.opt_in(
                email=email,
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'],
                sites=[Subscriber.default_site()],
                source=Subscription.SOURCE_KIOSK,
            )
            success = f'Thanks! {email} has been subscribed.'
            form = KioskSubscribeForm()
    else:
        form = KioskSubscribeForm()

    return render(request, 'subscribe/kiosk.html', {
        'form': form,
        'success': success,
        'failure': failure,
    })
