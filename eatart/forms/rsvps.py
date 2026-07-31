"""The RSVP form. Three answers and a headcount — nothing else is worth asking for."""
from django import forms

from gallery.forms import _captcha_field
from gallery.models import EventRsvp


class RsvpForm(forms.Form):
    """Name and email are taken from the account for somebody signed in, as on the visit form.

    They have told us both already, and reading them from the account rather than the post means
    a hidden field cannot be used to reply in another person's name.
    """

    response = forms.ChoiceField(choices=EventRsvp.RESPONSE_CHOICES,
                                 widget=forms.RadioSelect, initial=EventRsvp.YES,
                                 label='Are you coming?')
    name = forms.CharField(max_length=150, label='Your name')
    email = forms.EmailField(label='Your email address',
                             help_text='For the confirmation and a reminder the day before.')
    party_size = forms.IntegerField(
        min_value=1, max_value=20, initial=1, label='How many of you',
        help_text='Bring someone — openings are better with company.')
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                           label='Anything to add', help_text='Optional.')
    captcha = _captcha_field()

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.account = user if (user is not None and user.is_authenticated) else None
        if self.account:
            self.fields.pop('name')
            self.fields.pop('email')

    def account_name(self):
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
            cleaned['name'] = self.account_name()
            cleaned['email'] = self.account.email
        # A "no" with four people in the party is a contradiction, and the number would end up
        # in the maybe/coming arithmetic if anybody later widened what counts.
        if cleaned.get('response') == EventRsvp.NO:
            cleaned['party_size'] = 1
        return cleaned
