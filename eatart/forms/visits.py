"""The booking form. Small on purpose — a visit needs a name, a way to reach you, and a time."""
from django import forms
from django.utils import dateparse

from gallery.forms import _captcha_field


class VisitForm(forms.Form):
    when = forms.DateTimeField(widget=forms.HiddenInput)
    name = forms.CharField(max_length=150, label='Your name')
    email = forms.EmailField(label='Your email address',
                             help_text='Where the confirmation goes.')
    party_size = forms.IntegerField(
        min_value=1, max_value=20, initial=1, label='How many of you',
        help_text='Others may be visiting at the same time; the gallery is open, not booked out.')
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 3}), label='Anything to add',
        help_text='Optional — a work you want to see, access needs, running late.')
    captcha = _captcha_field()

    def clean_when(self):
        when = self.cleaned_data['when']
        if when is None:
            raise forms.ValidationError('Pick a time.')
        return when
