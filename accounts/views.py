import random
import string
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy

from django.views.generic.edit import FormView
from django.views.generic.edit import CreateView
from django.views.generic.edit import UpdateView
from allauth.account.views import SignupView, PasswordResetView

from accounts.roles import add_artist_role
from gallery.models import Artist
from gallery.permissions import is_staff_user

from .forms import ArtistRoleUpdateForm, ArtistUserCreateForm, CustomResetPasswordForm, CustomSignupForm, UserNameUpdateForm

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
        add_artist_role(user)

        messages.success(self.request, f'Successfully added { user.username }!')

        return redirect(self.request.path)

    def test_func(self):
        return is_staff_user(self.request.user)


class ArtistRoleUpdateView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = 'accounts/artist_role_edit.html'
    form_class = ArtistRoleUpdateForm

    def dispatch(self, request, *args, **kwargs):
        self.artist = get_object_or_404(Artist, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['artist'] = self.artist
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['artist'] = self.artist
        return context

    def form_valid(self, form):
        form.save()
        messages.success(self.request, f'Updated roles for {self.artist.full_name}.')
        return redirect(self.artist.get_absolute_url())

    def test_func(self):
        return is_staff_user(self.request.user)

    