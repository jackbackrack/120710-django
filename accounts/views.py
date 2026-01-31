import random
import string
from django.shortcuts import redirect
from django.contrib.auth.models import User, Group
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import render

from django.views.generic.edit import CreateView
from allauth.account.views import SignupView, PasswordResetView
from .forms import CustomSignupForm, CustomResetPasswordForm

class CustomPasswordResetView(PasswordResetView):
    form_class = CustomResetPasswordForm

class CustomSignupView(SignupView):
    form_class = CustomSignupForm

def artist_user_new_failure(request):
    return render(request, 'accounts/artist_user_new_failure.html')

def artist_user_new_success(request):
    return render(request, 'accounts/artist_user_new_success.html')

def generate_random_password(length=12):
    """Generate a random string of fixed length."""
    characters = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(characters) for i in range(length))

class ArtistUserCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = User
    fields = (
        "first_name",
        "last_name",
        "email",
        )
    template_name = "accounts/artist_user_new.html"

    def form_valid(self, form):
        user = form.save(commit=False)
        user.username = form.cleaned_data['email']
        user.set_password(generate_random_password())
        user.save()
        group = Group.objects.get(name='artist')
        user.groups.add(group)

        return redirect('accounts/artist_user_new_success')

    def test_func(self):
        return self.request.user.is_superuser

    