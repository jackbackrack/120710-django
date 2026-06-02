import random
import string
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from django.views.generic.edit import CreateView
from django.views.generic.edit import UpdateView
from allauth.account.views import SignupView, PasswordResetView

from gallery.models import Artist
from gallery.permissions import is_staff_user

from .forms import ArtistUserCreateForm, ClaimArtistForm, CustomResetPasswordForm, CustomSignupForm, LinkArtistToUserForm, UserNameUpdateForm

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

        messages.success(self.request, f'Successfully added { user.username }!')

        return redirect(self.request.path)

    def test_func(self):
        return is_staff_user(self.request.user)


@login_required
def claim_artist(request):
    artist = getattr(request.user, 'artists', None)
    if artist and artist.exists():
        messages.info(request, "You already have an artist profile linked to your account.")
        return redirect('gallery:artist_detail', slug=artist.first().slug)

    if request.method == 'POST':
        form = ClaimArtistForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            candidate = Artist.objects.filter(email__iexact=email, user__isnull=True).first()
            if candidate:
                candidate.user = request.user
                candidate.save(update_fields=['user'])
                messages.success(request, f'Artist profile "{candidate.name}" has been linked to your account.')
                return redirect('gallery:artist_detail', slug=candidate.slug)
            else:
                form.add_error('email', "No unlinked artist record was found with that email address.")
    else:
        form = ClaimArtistForm(initial={'email': request.user.email})

    return render(request, 'accounts/claim_artist.html', {'form': form})


def link_artist_to_user(request):
    if not is_staff_user(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == 'POST':
        form = LinkArtistToUserForm(request.POST)
        if form.is_valid():
            artist = form.cleaned_data['artist']
            user = form.cleaned_data['user']
            artist.user = user
            artist.save(update_fields=['user'])
            messages.success(request, f'Linked "{artist.name}" to {user.email}.')
            return redirect('link_artist_to_user')
    else:
        form = LinkArtistToUserForm()

    return render(request, 'accounts/link_artist_to_user.html', {'form': form})
