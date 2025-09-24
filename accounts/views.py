from django.shortcuts import render

from allauth.account.views import PasswordResetView
from .forms import CustomResetPasswordForm

class CustomPasswordResetView(PasswordResetView):
    form_class = CustomResetPasswordForm
