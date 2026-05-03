from allauth.account.forms import ResetPasswordForm, SignupForm
from django import forms
from django.contrib.auth.models import User
from django_recaptcha.fields import ReCaptchaField

from accounts.roles import (
    add_curator_role,
    add_juror_role,
    remove_curator_role,
    remove_juror_role,
)
from accounts.signup import ensure_signup_profile
from gallery.models import Artist, Tag


class CustomResetPasswordForm(ResetPasswordForm):
    captcha = ReCaptchaField()


class CustomSignupForm(SignupForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    captcha = ReCaptchaField()

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
        ensure_signup_profile(user)
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


class ArtistRoleUpdateForm(forms.Form):
    is_curator = forms.BooleanField(required=False, label='Curator access')
    is_juror = forms.BooleanField(required=False, label='Juror access')
    curator_tags = forms.ModelMultipleChoiceField(queryset=Tag.objects.none(), required=False)

    def __init__(self, *args, artist, **kwargs):
        self.artist = artist
        super().__init__(*args, **kwargs)
        self.fields['curator_tags'].queryset = Tag.objects.order_by('name')

        if self.artist.user_id:
            self.initial['is_curator'] = self.artist.user.groups.filter(name='curator').exists()
            self.initial['is_juror'] = self.artist.user.groups.filter(name='juror').exists()
            self.initial['curator_tags'] = self.artist.user.curator_tags.all()

    def clean(self):
        cleaned_data = super().clean()
        if not self.artist.user_id:
            raise forms.ValidationError('The selected artist must be linked to a user account before roles can be updated.')
        return cleaned_data

    def save(self):
        user = self.artist.user
        if self.cleaned_data['is_curator']:
            add_curator_role(user)
        else:
            remove_curator_role(user)

        if self.cleaned_data['is_juror']:
            add_juror_role(user)
        else:
            remove_juror_role(user)

        user.curator_tags.set(self.cleaned_data['curator_tags'])
        return user