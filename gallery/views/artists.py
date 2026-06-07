from eatart.schemaorg.mappers import artist_to_schema

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from django.urls import reverse_lazy

from gallery.forms import ArtistForm
from gallery.models import Artist, Tag
from gallery.permissions import (
    can_manage_artist,
    is_artist_user,
    is_staff_user,
    tag_filter_queryset,
    visible_artist_queryset,
    visible_artwork_queryset,
)
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin


class ArtistListView(ListView):
    model = Artist
    template_name = 'gallery/artist_list.html'
    paginate_by = 100

    def get_queryset(self):
        queryset = Artist.objects.filter(visible_artist_queryset(self.request.user)).prefetch_related('tags')
        return tag_filter_queryset(queryset, self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        artists = list(context['object_list'])
        context['artist_list'] = artists
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        context['can_manage_artist'] = {a.id for a in artists if can_manage_artist(self.request.user, a)}
        return context


class ArtistMailChimpView(ListView, LoginRequiredMixin, UserPassesTestMixin):
    model = Artist
    template_name = 'gallery/artist_mailchimp_list.html'

    def test_func(self):
        return is_staff_user(self.request.user)


class ArtistDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Artist
    schema_mapper = artist_to_schema
    template_name = 'gallery/artist_detail.html'

    def get_queryset(self):
        return Artist.objects.filter(visible_artist_queryset(self.request.user)).distinct()

    def get_context_data(self, **kwargs):
        from gallery.permissions import can_manage_artist, can_manage_artwork, can_see_all_shows
        from gallery.models.submissions import ArtworkSubmission
        from gallery.models import Show
        context = super().get_context_data(**kwargs)
        artist = self.object
        artworks = artist.artworks.filter(visible_artwork_queryset(self.request.user)).prefetch_related('shows').distinct()
        context['artworks'] = artworks
        context['can_manage_artist'] = can_manage_artist(self.request.user, artist)
        context['can_manage_artwork'] = {a.id for a in artworks if can_manage_artwork(self.request.user, a)}
        user = self.request.user
        if user.is_authenticated and artist.user == user:
            context['my_submissions'] = (
                ArtworkSubmission.objects
                .filter(submitted_by=user)
                .select_related('artwork', 'show')
                .order_by('-submitted_at')
            )
        from gallery.permissions import can_manage_show
        shows_qs = Show.objects.filter(artworks__artists=artist).prefetch_related('curators', 'tags').distinct()
        if not can_see_all_shows(user):
            shows_qs = shows_qs.filter(status__in=Show.PUBLIC_STATUSES)
        shows = list(shows_qs.order_by('name'))
        context['shows'] = shows
        context['can_manage_show'] = {s.id for s in shows if can_manage_show(user, s)}
        return context


class ArtistUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Artist
    form_class = ArtistForm
    template_name = 'gallery/artist_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        return super().form_valid(form)

    def test_func(self):
        obj = self.get_object()
        return can_manage_artist(self.request.user, obj)


class ArtistDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Artist
    template_name = 'gallery/artist_delete.html'
    success_url = reverse_lazy('gallery:artist_list')

    def test_func(self):
        obj = self.get_object()
        return can_manage_artist(self.request.user, obj)


class ArtistCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Artist
    form_class = ArtistForm
    template_name = 'gallery/artist_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)

    def test_func(self):
        return not self.request.user.artists.exists()
