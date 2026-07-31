"""The booking form. Small on purpose — a visit needs a name, a way to reach you, and a time."""
from django import forms
from django.utils import dateparse

from gallery.forms import _captcha_field


class VisitForm(forms.Form):
    """Name and email are filled in for somebody already signed in, and not asked for again.

    They have told us both already — their name is on their profile and the address is the one
    they signed in with — so asking is friction that buys nothing. The values are taken from the
    account on the server rather than from the posted form, so a signed-in booking cannot be made
    in somebody else's name by editing a hidden field.
    """

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

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.account = user if (user is not None and user.is_authenticated) else None
        if self.account:
            # Dropped rather than pre-filled and disabled: a disabled field posts nothing and a
            # pre-filled one can be edited, and neither is what "we already know this" means.
            self.fields.pop('name')
            self.fields.pop('email')

    def account_name(self):
        """Their name as the gallery knows it, falling back to the account itself."""
        user = self.account
        artist = getattr(user, 'artist', None) or getattr(user, 'artist_profile', None)
        for candidate in (getattr(artist, 'name', None),
                          user.get_full_name(), user.first_name, user.username):
            if candidate and str(candidate).strip():
                return str(candidate).strip()[:150]
        return user.email

    def clean(self):
        cleaned = super().clean()
        if self.account:
            # From the account, never from the request: otherwise a hidden field would let a
            # signed-in visitor book under somebody else's name.
            cleaned['name'] = self.account_name()
            cleaned['email'] = self.account.email
        return cleaned

    def clean_when(self):
        when = self.cleaned_data['when']
        if when is None:
            raise forms.ValidationError('Pick a time.')
        return when
