from django.shortcuts import render

from allauth.account.views import SignupView, PasswordResetView
from .forms import CustomSignupForm, CustomResetPasswordForm

# from django.contrib.auth.views import PasswordResetView
from django.core.mail import send_mail

class CustomPasswordResetView(PasswordResetView):
    form_class = CustomResetPasswordForm
    def send_mail(self, subject_template_name, email_template_name,
                  context, from_email, to_email, html_email_template_name=None):
        # Customize the send_mail call here
        send_mail(
            subject=self.get_email_subject(subject_template_name, context),
            message=self.get_email_message(email_template_name, context),
            from_email=from_email,
            recipient_list=[to_email],
            fail_silently=False,  # Set fail_silently to False
            html_message=self.get_email_message(html_email_template_name, context) if html_email_template_name else None,
        )

# class CustomPasswordResetView(PasswordResetView):
#     form_class = CustomResetPasswordForm

class CustomSignupView(SignupView):
    form_class = CustomSignupForm
    