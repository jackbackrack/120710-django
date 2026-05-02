from django import forms
from django_recaptcha.fields import ReCaptchaField


class SubscribeForm(forms.Form):
    first_name = forms.CharField(label='First Name', max_length=100)
    last_name = forms.CharField(label='Last Name', max_length=100)
    email = forms.EmailField(label='Email')
    address = forms.CharField(
        required=False,
        label="If you're a human, you're awesome, and leave this invisible field blank.",
        widget=forms.TextInput(attrs={'tabindex': '-1', 'class': 'honeypot'}),
    )
    captcha = ReCaptchaField()
