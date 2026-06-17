from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from honeypot.decorators import check_honeypot
from mailchimp_marketing.api_client import ApiClientError

from eatart.forms.subscribe import KioskSubscribeForm, SubscribeForm
from eatart.services.mailchimp import subscribe_to_mailing_list


@check_honeypot()
def subscribe(request):
    if request.method == 'POST':
        form = SubscribeForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            try:
                subscribe_to_mailing_list(
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    email=email,
                )
                messages.success(request, f'Successfully subscribed {email}!')
            except ApiClientError:
                messages.error(request, f'Failed to subscribe {email}!')

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
            try:
                subscribe_to_mailing_list(
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    email=email,
                )
                success = f'Thanks! {email} has been subscribed.'
            except ApiClientError:
                failure = f'Something went wrong — {email} could not be subscribed.'
            form = KioskSubscribeForm()
    else:
        form = KioskSubscribeForm()

    return render(request, 'subscribe/kiosk.html', {
        'form': form,
        'success': success,
        'failure': failure,
    })
