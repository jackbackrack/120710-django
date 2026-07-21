import json
import urllib.parse
import urllib.request

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy

from gallery.models import Site, Show, Artist, Artwork
from gallery.models.room import RoomConfig
from gallery.forms import SiteForm, RoomConfigForm, _make_obstacle_formset, _make_support_formset
from gallery.permissions import is_staff_user

ROOM_DEFAULTS = {'width_in': 384, 'depth_in': 576, 'height_in': 120}


class RoomConfigMixin:
    """Adds a RoomConfig form + WallObstacle inline formset to a Site create/update view."""

    def _get_room_config(self, site):
        room_config, _ = RoomConfig.objects.get_or_create(site=site, defaults=ROOM_DEFAULTS)
        return room_config

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # self.object is None on create (site not yet saved); use an unsaved RoomConfig.
        room_config = self._get_room_config(self.object) if self.object else RoomConfig(**ROOM_DEFAULTS)
        ObstacleFormSet = _make_obstacle_formset()
        SupportFormSet = _make_support_formset()
        if self.request.method == 'POST':
            context.setdefault('room_form', RoomConfigForm(self.request.POST, self.request.FILES, instance=room_config))
            context.setdefault('obstacle_formset', ObstacleFormSet(self.request.POST, instance=room_config))
            context.setdefault('support_formset', SupportFormSet(self.request.POST, self.request.FILES, instance=room_config, prefix='supports'))
        else:
            context.setdefault('room_form', RoomConfigForm(instance=room_config))
            context.setdefault('obstacle_formset', ObstacleFormSet(instance=room_config))
            context.setdefault('support_formset', SupportFormSet(instance=room_config, prefix='supports'))
        return context

    def form_valid(self, form):
        response = super().form_valid(form)   # saves the Site → self.object
        room_config = self._get_room_config(self.object)
        room_form = RoomConfigForm(self.request.POST, self.request.FILES, instance=room_config)
        ObstacleFormSet = _make_obstacle_formset()
        SupportFormSet = _make_support_formset()
        obstacle_formset = ObstacleFormSet(self.request.POST, instance=room_config)
        support_formset = SupportFormSet(self.request.POST, self.request.FILES, instance=room_config, prefix='supports')

        # Save each section independently (the RoomConfig is already persisted via
        # get_or_create) so a problem in one — e.g. an obstacle typo — no longer
        # silently discards the others, such as a support deletion.
        all_ok = True
        if room_form.is_valid():
            room_form.save()
        else:
            all_ok = False
        if obstacle_formset.is_valid():
            obstacle_formset.save()
        else:
            all_ok = False
        if support_formset.is_valid():
            support_formset.save()
        else:
            all_ok = False

        if all_ok:
            return response

        # Re-render with errors (Site + any valid sections are already saved).
        messages.error(self.request, 'Some room/support changes could not be saved — please fix the highlighted fields.')
        return self.render_to_response(self.get_context_data(
            form=form, room_form=room_form, obstacle_formset=obstacle_formset,
            support_formset=support_formset,
        ))


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
        shows = (
            site.shows.filter(status__in=Show.PUBLIC_STATUSES)
            .prefetch_related('curators', 'sites', 'events')
            .order_by(F('start').desc(nulls_last=True), '-created_at')
        )
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
        ).distinct().order_by('-created_at')
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
        context['artworks'] = (
            Artwork.objects.filter(shows__sites=site)
            .prefetch_related('artists', 'shows')
            .distinct()
            .order_by('-created_at')
        )
        return context


class SiteCreateView(LoginRequiredMixin, UserPassesTestMixin, RoomConfigMixin, CreateView):
    model = Site
    form_class = SiteForm
    template_name = 'gallery/site_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def test_func(self):
        return is_staff_user(self.request.user)


class SiteUpdateView(LoginRequiredMixin, UserPassesTestMixin, RoomConfigMixin, UpdateView):
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
