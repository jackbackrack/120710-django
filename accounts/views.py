from django.shortcuts import render

from allauth.account.views import SignupView, PasswordResetView
from .forms import CustomSignupForm, CustomResetPasswordForm

class CustomPasswordResetView(PasswordResetView):
    form_class = CustomResetPasswordForm

class CustomSignupView(SignupView):
    form_class = CustomSignupForm
    