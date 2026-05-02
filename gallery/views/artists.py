from eatart.schemaorg.mappers import artist_to_schema

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from django.urls import reverse_lazy

from gallery.forms import ArtistForm
from gallery.models import Artist, Tag
from gallery.permissions import can_manage_artist, is_artist_user, is_staff_user, tag_filter_queryset, visible_artwork_queryset
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin


class ArtistListView(ListView):
    model = Artist
    template_name = 'gallery/artist_list.html'

    def get_queryset(self):
        queryset = Artist.objects.all()
        return tag_filter_queryset(queryset, self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        artist = self.object
        artworks = artist.artworks.filter(visible_artwork_queryset(self.request.user)).distinct()
        context['artworks'] = artworks
        context['can_update_roles'] = is_staff_user(self.request.user) and artist.user_id
        return context


class ArtistUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Artist
    form_class = ArtistForm
    template_name = 'gallery/artist_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

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
        user = self.request.user
        return not user.artists.exists() and is_artist_user(user)
