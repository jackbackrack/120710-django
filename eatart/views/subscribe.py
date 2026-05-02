from django.contrib import messages
from django.shortcuts import redirect, render

from honeypot.decorators import check_honeypot
from mailchimp_marketing.api_client import ApiClientError

from eatart.forms.subscribe import SubscribeForm
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
