from django.shortcuts import render, redirect
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit
from datetime import datetime
from mailchimp_marketing import Client
from mailchimp_marketing.api_client import ApiClientError
from django.conf import settings
from django_recaptcha.fields import ReCaptchaField
from honeypot.decorators import check_honeypot

from piece.models import Piece, Artist, Show, Event

def index(request):
    pieces = Piece.objects.filter()[0:6]
    shows = Show.objects.filter().order_by('-start')
    artists = Artist.objects.filter().order_by('name')
    now = datetime.now()
    print(now)
    current_show = Show.objects.filter(start__lte=now, end__gte=now).first()
    is_current_show = False
    is_next_show = False
    next_show = None
    next_event = Event.objects.filter(date__gte=now).order_by('date').first()
    print(next_event)

    if current_show:
        next_show = current_show
        is_current_show = True
    else:
        next_show = Show.objects.filter(start__gt=now).order_by('start').first()
        if next_show:
            is_next_show = True

    return render(request, 'market/index.html', {
        'is_current_show': is_current_show,
        'is_next_show': is_next_show,
        'next_show': next_show,
        'next_event': next_event,
        'pieces': pieces,
        'shows': shows,
        'artists': artists,
    })

def contact(request):
    return render(request, 'market/contact.html')

def visit(request):
    return render(request, 'market/visit.html')

def about(request):
    return render(request, 'market/about.html')

def howto(request):
    return render(request, 'market/howto.html')

class SubscribeForm(forms.Form):
    first_name = forms.CharField(label="First Name", max_length=100)
    last_name = forms.CharField(label="Last Name", max_length=100)
    email = forms.EmailField(label="Email")
    address = forms.CharField(
        required=False,
        label="If you're a human, you're awesome, and leave this invisible field blank.",
        widget=forms.TextInput(attrs={'tabindex': '-1', 'class': 'honeypot'}))
    captcha = ReCaptchaField()

#    def __init__(self, *args, **kwargs):
#        super().__init__(*args, **kwargs)
#        self.helper = FormHelper()
#        self.helper.layout = Layout(
#            'first_name',
#            'last_name',
#            'email',
#            'captcha',
#            Submit('submit', 'Submit')
#            # Exclude 'address' from the layout to not render it
#        )
@check_honeypot()
def subscribe(request):
    if request.method == 'POST':
        form = SubscribeForm(request.POST)
        if form.is_valid():
            # Access cleaned data
            first_name = form.cleaned_data['first_name']
            last_name = form.cleaned_data['last_name']
            email = form.cleaned_data['email']

            # initializing the mailchimp client with api key
            mailchimpClient = Client()
            mailchimpClient.set_config({
                "api_key": settings.MAILCHIMP_API_KEY,
                "server":  settings.MAILCHIMP_DATA_CENTER,
            })

            userInfo = {
                "email_address": email,
                "status": "subscribed",
                "merge_fields": {
                    "FNAME": first_name,
                    "LNAME": last_name
                }
            }

            try:
                # adding member to mailchimp audience list
                mailchimpClient.lists.add_list_member(settings.MAILCHIMP_AUDIENCE_ID, userInfo)
                return redirect("subscribe_success")
            except ApiClientError as error:
                return redirect("subscribe_failure")
    else:
        form = SubscribeForm

    return render(request, "market/subscribe.html", {'form': form})


def subscribe_failure(request):
    return render(request, 'market/subscribe_failure.html')

def subscribe_success(request):
    return render(request, 'market/subscribe_success.html')

