import random
import string
from django.contrib import messages
from django.contrib.auth.models import User, Group
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy

from django.views.generic.edit import CreateView
from django.views.generic.edit import UpdateView
from allauth.account.views import SignupView, PasswordResetView
from .forms import ArtistUserCreateForm, CustomResetPasswordForm, CustomSignupForm, UserNameUpdateForm

class CustomPasswordResetView(PasswordResetView):
    form_class = CustomResetPasswordForm

class CustomSignupView(SignupView):
    form_class = CustomSignupForm


class UserNameUpdateView(LoginRequiredMixin, UpdateView):
    model = User
    form_class = UserNameUpdateForm
    template_name = "account/profile.html"
    success_url = reverse_lazy("account_profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Your name has been updated.")
        return response

def generate_random_password(length=12):
    """Generate a random string of fixed length."""
    characters = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(characters) for i in range(length))

class ArtistUserCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = User
    form_class = ArtistUserCreateForm
    template_name = "accounts/artist_user_new.html"

    def form_valid(self, form):
        user = form.save(commit=False)
        user.username = form.cleaned_data['email']
        user.set_password(generate_random_password())
        user.save()
        group = Group.objects.get(name='artist')
        user.groups.add(group)

        messages.success(self.request, f'Successfully added { user.username }!')

        return redirect(self.request.path)

    def test_func(self):
        return self.request.user.is_superuser

    