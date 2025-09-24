from allauth.account.forms import SignupForm, ResetPasswordForm
from django_recaptcha.fields import ReCaptchaField

class CustomResetPasswordForm(ResetPasswordForm):
    captcha = ReCaptchaField()

class CustomSignupForm(SignupForm):
    captcha = ReCaptchaField()    