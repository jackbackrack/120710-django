from allauth.account.forms import ResetPasswordForm, SignupForm
from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django_recaptcha.fields import ReCaptchaField

from accounts.signup import ensure_signup_profile


def _captcha_field():
    if getattr(settings, 'RECAPTCHA_ENABLED', False):
        return ReCaptchaField()
    return forms.CharField(required=False, widget=forms.HiddenInput)


class CustomResetPasswordForm(ResetPasswordForm):
    captcha = _captcha_field()


class CustomSignupForm(SignupForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    captcha = _captcha_field()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sociallogin = getattr(self, "sociallogin", None)
        extra_data = getattr(getattr(sociallogin, "account", None), "extra_data", {})
        self.fields["first_name"].initial = extra_data.get("given_name", self.fields["first_name"].initial)
        self.fields["last_name"].initial = extra_data.get("family_name", self.fields["last_name"].initial)

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.save(update_fields=["first_name", "last_name"])
        # Mandatory email verification sends them off to their inbox and back via a
        # confirm link, which lands on a bare login page — ?next= does not survive
        # that. Park the destination in the session so the eventual login still
        # finishes the journey they started.
        nxt = request.POST.get('next') or request.GET.get('next')
        if nxt:
            request.session['post_auth_next'] = nxt
        artist, status = ensure_signup_profile(user)
        if artist and status == 'claimed':
            request.session['claimed_artist_pk'] = artist.pk
        elif artist and status == 'created':
            request.session['new_artist_pk'] = artist.pk
        return user


class ArtistUserCreateForm(forms.ModelForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
        )


class ClaimArtistForm(forms.Form):
    email = forms.EmailField(
        label="Your email address",
        help_text="Enter the email address associated with your artist record.",
    )


class LinkArtistToUserForm(forms.Form):
    artist = forms.ModelChoiceField(
        queryset=None,
        label="Artist record",
        empty_label="— select artist —",
    )
    user = forms.ModelChoiceField(
        queryset=None,
        label="User account",
        empty_label="— select user —",
    )

    def __init__(self, *args, **kwargs):
        from gallery.models import Artist
        super().__init__(*args, **kwargs)
        self.fields['artist'].queryset = Artist.objects.filter(user__isnull=True).order_by('name')
        self.fields['user'].queryset = User.objects.order_by('email')


class UserNameUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
