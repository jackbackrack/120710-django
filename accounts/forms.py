from allauth.account.forms import ResetPasswordForm, SignupForm
from django import forms
from django.contrib.auth.models import User
from django_recaptcha.fields import ReCaptchaField


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