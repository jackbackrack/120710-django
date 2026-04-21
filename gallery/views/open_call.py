from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q
from django.views.generic import TemplateView

from gallery.models import Artwork, Show
from gallery.permissions import is_curator_user


class OpenCallDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'gallery/open_call_dashboard.html'

    def test_func(self):
        return is_curator_user(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()

        shows = Show.objects.filter(is_open_call=True).select_related('managing_curator').prefetch_related('artists', 'tags').order_by('-start')
        artworks = Artwork.objects.filter(open_call_available=True).prefetch_related('artists', 'tags', 'shows').order_by('-created_at').distinct()

        if query:
            shows = shows.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(artists__name__icontains=query)
            ).distinct()
            artworks = artworks.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(artists__name__icontains=query)
                | Q(medium__icontains=query)
            ).distinct()

        context['query'] = query
        context['open_call_shows'] = shows
        context['open_call_artworks'] = artworks
        return context


class ArtistOpenCallView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'gallery/open_call_artist.html'

    def test_func(self):
        return self.request.user.artists.exists()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        artist = self.request.user.artists.order_by('-created_at').first()
        query = self.request.GET.get('q', '').strip()

        shows = Show.objects.filter(is_open_call=True).select_related('managing_curator').prefetch_related('artists', 'tags').order_by('-start')
        artworks = Artwork.objects.filter(artists=artist, open_call_available=True).prefetch_related('artists', 'tags', 'shows').order_by('-created_at').distinct()

        if query:
            shows = shows.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(tags__name__icontains=query)
            ).distinct()
            artworks = artworks.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(medium__icontains=query)
                | Q(tags__name__icontains=query)
            ).distinct()

        context['artist'] = artist
        context['query'] = query
        context['open_call_shows'] = shows
        context['artist_open_call_artworks'] = artworks
        return context