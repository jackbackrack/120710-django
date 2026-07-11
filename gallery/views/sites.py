import json
import urllib.parse
import urllib.request

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy

from gallery.models import Site, Show, Artist, Artwork
from gallery.forms import SiteForm
from gallery.permissions import is_staff_user


@require_POST
def geocode_address(request):
    parts = [
        request.POST.get('street', ''),
        request.POST.get('city', ''),
        request.POST.get('state', ''),
        request.POST.get('postal_code', ''),
        request.POST.get('country', ''),
    ]
    address = ', '.join(p for p in parts if p.strip())
    if not address:
        return JsonResponse({'error': 'No address provided.'}, status=400)

    params = urllib.parse.urlencode({'q': address, 'format': 'json', 'limit': 1})
    url = f'https://nominatim.openstreetmap.org/search?{params}'
    req = urllib.request.Request(url, headers={'User-Agent': '120710.art gallery site locator'})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return JsonResponse({'error': f'Geocoding request failed: {e}'}, status=502)

    if not data:
        return JsonResponse({'error': 'Address not found — try adding more detail.'}, status=404)

    result = data[0]
    return JsonResponse({
        'latitude': result['lat'],
        'longitude': result['lon'],
        'display_name': result['display_name'],
    })


class SiteListView(ListView):
    model = Site
    template_name = 'gallery/site_list.html'
    context_object_name = 'sites'

    def get_queryset(self):
        qs = Site.objects.all()
        if not (self.request.user.is_authenticated and is_staff_user(self.request.user)):
            qs = qs.filter(status=Site.STATUS_PUBLISHED)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        mapped = [
            {'name': s.name, 'url': s.get_absolute_url(), 'lat': float(s.latitude), 'lng': float(s.longitude)}
            for s in context['sites']
            if s.latitude is not None and s.longitude is not None
        ]
        context['sites_json'] = json.dumps(mapped)
        return context


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
