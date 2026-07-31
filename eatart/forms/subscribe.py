from django import forms
from django.conf import settings
from django_recaptcha.fields import ReCaptchaField


def _captcha_field():
    if getattr(settings, 'RECAPTCHA_ENABLED', False):
        return ReCaptchaField()
    return forms.CharField(required=False, widget=forms.HiddenInput)


def _interests_field():
    """Optional, and never a blocker.

    Three ticks nobody has to make. The value of knowing is that an open call can go to
    artists instead of to everybody, and the cost of asking has to stay near zero: required
    here would trade real subscribers for a nicer database.
    """
    from gallery.models import Subscriber
    return forms.MultipleChoiceField(
        choices=Subscriber.INTEREST_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Anything that applies (optional)',
        help_text='Lets us send you the things you actually want. Leave it blank and you '
                  'just get the news.',
    )


class SubscribeForm(forms.Form):
    first_name = forms.CharField(label='First Name', max_length=100)
    last_name = forms.CharField(label='Last Name', max_length=100)
    email = forms.EmailField(label='Email')
    interests = _interests_field()
    address = forms.CharField(
        required=False,
        label="If you're a human, you're awesome, and leave this invisible field blank.",
        widget=forms.TextInput(attrs={'tabindex': '-1', 'class': 'honeypot'}),
    )
    captcha = _captcha_field()


class KioskSubscribeForm(forms.Form):
    first_name = forms.CharField(label='First Name', max_length=100)
    last_name = forms.CharField(label='Last Name', max_length=100)
    email = forms.EmailField(label='Email')
    interests = _interests_field()
