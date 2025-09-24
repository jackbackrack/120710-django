from allauth.account.forms import ResetPasswordForm
from django_recaptcha.fields import ReCaptchaField

class CustomResetPasswordForm(ResetPasswordForm):
    captcha = ReCaptchaField()