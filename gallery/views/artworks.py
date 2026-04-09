from eatart.schemaorg.mappers import artwork_to_schema

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from gallery.forms import ArtworkForm
from gallery.models import Artwork, Tag
from gallery.permissions import can_manage_artwork, is_artist_user, is_staff_user, tag_filter_queryset, visible_artwork_queryset
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin


def detail(request, pk):
    artwork = get_object_or_404(Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct(), pk=pk)
    return render(request, 'gallery/artwork_detail.html', {'artwork': artwork})


class ArtworkListView(ListView):
    model = Artwork
    context_object_name = 'artwork_list'
    template_name = 'gallery/artwork_list.html'

    def get_queryset(self):
        queryset = Artwork.objects.filter(visible_artwork_queryset(self.request.user)).distinct()
        return tag_filter_queryset(queryset, self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        return context


class ArtworkDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Artwork
    context_object_name = 'artwork'
    schema_mapper = artwork_to_schema
    template_name = 'gallery/artwork_detail.html'

    def get_queryset(self):
        return Artwork.objects.filter(visible_artwork_queryset(self.request.user)).distinct()


class ArtworkUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Artwork
    form_class = ArtworkForm
    template_name = 'gallery/artwork_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        if not is_staff_user(self.request.user):
            form.instance.created_by = self.request.user
            form.instance.is_public = False
        response = super().form_valid(form)
        if self.request.user.is_authenticated and self.request.user.artists.exists():
            self.object.artists.add(self.request.user.artists.order_by('-created_at').first())
        return response

    def test_func(self):
        obj = self.get_object()
        return can_manage_artwork(self.request.user, obj)


class ArtworkDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Artwork
    template_name = 'gallery/artwork_delete.html'
    success_url = reverse_lazy('gallery:artwork_list')

    def test_func(self):
        obj = self.get_object()
        return can_manage_artwork(self.request.user, obj)


class ArtworkCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Artwork
    form_class = ArtworkForm
    template_name = 'gallery/artwork_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        if not is_staff_user(self.request.user):
            form.instance.created_by = self.request.user
            form.instance.is_public = False
        response = super().form_valid(form)
        artist = self.request.user.artists.order_by('-created_at').first()
        if artist:
            self.object.artists.add(artist)
        return response

    def test_func(self):
        return is_artist_user(self.request.user)
