from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy

from gallery.models import Site, Show, Artist, Artwork
from gallery.forms import SiteForm
from gallery.permissions import is_staff_user


class SiteListView(ListView):
    model = Site
    template_name = 'gallery/site_list.html'
    context_object_name = 'sites'

    def get_queryset(self):
        qs = Site.objects.all()
        if not (self.request.user.is_authenticated and is_staff_user(self.request.user)):
            qs = qs.filter(status=Site.STATUS_PUBLISHED)
        return qs


class SiteDetailView(DetailView):
    model = Site
    template_name = 'gallery/site_detail.html'
    context_object_name = 'site'

    def get_queryset(self):
        qs = Site.objects.all()
        if not (self.request.user.is_authenticated and is_staff_user(self.request.user)):
            qs = qs.filter(status=Site.STATUS_PUBLISHED)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = self.object
        shows = site.shows.filter(
            status__in=Show.PUBLIC_STATUSES
        ).order_by('-start')
        context['shows'] = shows
        context['can_manage_site'] = (
            self.request.user.is_authenticated and
            is_staff_user(self.request.user)
        )
        return context


class SiteArtistListView(DetailView):
    model = Site
    template_name = 'gallery/site_artist_list.html'
    context_object_name = 'site'

    def get_queryset(self):
        qs = Site.objects.all()
        if not (self.request.user.is_authenticated and is_staff_user(self.request.user)):
            qs = qs.filter(status=Site.STATUS_PUBLISHED)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = self.object
        context['artists'] = Artist.objects.filter(
            artworks__shows__sites=site
        ).distinct().order_by('name')
        return context


class SiteArtworkListView(DetailView):
    model = Site
    template_name = 'gallery/site_artwork_list.html'
    context_object_name = 'site'

    def get_queryset(self):
        qs = Site.objects.all()
        if not (self.request.user.is_authenticated and is_staff_user(self.request.user)):
            qs = qs.filter(status=Site.STATUS_PUBLISHED)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = self.object
        context['artworks'] = Artwork.objects.filter(
            shows__sites=site
        ).distinct().order_by('name')
        return context


class SiteCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Site
    form_class = SiteForm
    template_name = 'gallery/site_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def test_func(self):
        return is_staff_user(self.request.user)


class SiteUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Site
    form_class = SiteForm
    template_name = 'gallery/site_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def test_func(self):
        return is_staff_user(self.request.user)


class SiteDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Site
    template_name = 'gallery/site_delete.html'
    success_url = reverse_lazy('gallery:site_list')

    def test_func(self):
        return is_staff_user(self.request.user)
