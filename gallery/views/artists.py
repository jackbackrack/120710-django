from eatart.schemaorg.mappers import artist_to_schema

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from gallery.forms import ArtistForm
from gallery.models import Artist, Tag
from django.db.models import Max

from gallery.permissions import (
    can_delete_artist,
    can_manage_artist,
    is_artist_user,
    is_curator_user,
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
        context['can_delete_artist'] = {a.id for a in artists if can_delete_artist(self.request.user, a)}
        return context


class ArtistMailChimpView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Artist
    template_name = 'gallery/artist_mailchimp_list.html'

    def test_func(self):
        return is_staff_user(self.request.user)


@login_required
def artist_email_list(request):
    if not (is_staff_user(request.user) or is_curator_user(request.user)):
        raise Http404

    User = get_user_model()

    # Artists with email addresses, annotated with latest artwork upload
    artists = (
        Artist.objects
        .filter(email__isnull=False)
        .exclude(email='')
        .annotate(latest_artwork=Max('artworks__created_at'))
        .select_related('user')
    )
    rows = []
    for a in artists:
        date = a.latest_artwork or (a.user.date_joined if a.user else None)
        rows.append({'name': a.name, 'email': a.email, 'date': date,
                     'date_is_artwork': bool(a.latest_artwork), 'url': a.get_absolute_url()})

    # Users with no artist record who have email addresses — use account creation date
    artist_user_ids = Artist.objects.filter(user__isnull=False).values_list('user_id', flat=True)
    orphan_users = (
        User.objects
        .filter(is_active=True)
        .exclude(email='')
        .exclude(pk__in=artist_user_ids)
    )
    for u in orphan_users:
        rows.append({'name': u.get_full_name() or u.email, 'email': u.email,
                     'date': u.date_joined, 'date_is_artwork': False, 'url': None})

    # Sort newest first; rows with no date at the bottom
    rows.sort(key=lambda r: (r['date'] is None, -(r['date'].timestamp() if r['date'] else 0)))

    return render(request, 'gallery/artist_email_list.html', {'rows': rows})


class ArtistDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Artist
    schema_mapper = artist_to_schema
    template_name = 'gallery/artist_detail.html'

    def get_queryset(self):
        return Artist.objects.filter(visible_artist_queryset(self.request.user)).distinct()

    def get(self, request, *args, **kwargs):
        if self.kwargs.get(self.pk_url_kwarg) is not None:
            return super().get(request, *args, **kwargs)
        self.object = self.get_object()
        if (request.user.is_authenticated
                and self.object.user == request.user
                and not self.object.image):
            return redirect(reverse('gallery:artist_edit', kwargs={'pk': self.object.pk}))
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        from gallery.permissions import can_delete_artist, can_delete_artwork, can_manage_artist, can_manage_artwork, is_curator_user, visible_show_queryset
        from gallery.models.submissions import ArtworkSubmission
        from gallery.models import Show
        context = super().get_context_data(**kwargs)
        artist = self.object
        artworks = artist.artworks.filter(visible_artwork_queryset(self.request.user)).prefetch_related('artists', 'shows', 'shows__curators').distinct()
        context['artworks'] = artworks
        context['can_manage_artist'] = can_manage_artist(self.request.user, artist)
        context['can_delete_artist'] = can_delete_artist(self.request.user, artist)
        context['can_see_contact'] = (
            can_manage_artist(self.request.user, artist) or
            is_curator_user(self.request.user)
        )
        context['can_manage_artwork'] = {a.id for a in artworks if can_manage_artwork(self.request.user, a)}
        context['can_delete_artwork'] = {a.id for a in artworks if can_delete_artwork(self.request.user, a)}
        user = self.request.user
        if user.is_authenticated and artist.user == user:
            context['my_submissions'] = (
                ArtworkSubmission.objects
                .filter(submitted_by=user)
                .select_related('artwork', 'show')
                .order_by('-submitted_at')
            )
            open_call_shows = Show.objects.filter(status=Show.STATUS_OPEN_CALL).prefetch_related('curators')
            submittable_shows = []
            for show in open_call_shows:
                if show.submission_type == Show.SUBMISSION_OPEN:
                    submittable_shows.append(show)
                elif show.submission_type == Show.SUBMISSION_INVITED:
                    if show.invitations.filter(email__iexact=user.email).exists():
                        submittable_shows.append(show)
            context['submittable_shows'] = submittable_shows
            missing = []
            if not artist.image:
                missing.append('photo')
            if not artist.first_name:
                missing.append('first name')
            if not artist.last_name:
                missing.append('last name')
            if not artist.zipcode:
                missing.append('zip code')
            context['profile_complete'] = not missing
            context['missing_profile_fields'] = missing
            context['submittable_show_ids'] = {s.id for s in submittable_shows} if not missing else set()
        from gallery.permissions import can_delete_show, can_manage_show
        shows_qs = Show.objects.filter(artworks__artists=artist).prefetch_related('curators', 'tags', 'events').distinct()
        shows_qs = visible_show_queryset(shows_qs, user)
        shows = list(shows_qs.order_by('name'))
        context['shows'] = shows
        context['can_manage_show'] = {s.id for s in shows if can_manage_show(user, s)}
        context['can_delete_show'] = {s.id for s in shows if can_delete_show(user, s)}
        if user.is_authenticated and artist.user == user:
            curated_shows = list(
                Show.objects.filter(curators=artist)
                .prefetch_related('curators', 'tags', 'events')
                .order_by('-start')
            )
            context['curated_shows'] = curated_shows
            all_shows = shows + curated_shows
            context['can_manage_show'] = {s.id for s in all_shows if can_manage_show(user, s)}
            context['can_delete_show'] = {s.id for s in all_shows if can_delete_show(user, s)}
        from gallery.models.collection import CollectionPiece, SavedArtwork
        confirmed_pieces = (
            CollectionPiece.objects
            .filter(collector=artist.user, status=CollectionPiece.STATUS_CONFIRMED)
            .select_related('artwork')
            .prefetch_related('artwork__artists')
            .order_by('display_order', '-created_at')
            if artist.user else CollectionPiece.objects.none()
        )
        context['collection'] = confirmed_pieces
        if user.is_authenticated and artist.user == user:
            context['pending_confirmations'] = (
                CollectionPiece.objects
                .filter(
                    artwork__artists=artist,
                    status=CollectionPiece.STATUS_PENDING,
                )
                .select_related('collector', 'artwork')
                .order_by('-created_at')
            )
            context['saved_artworks'] = (
                SavedArtwork.objects
                .filter(user=user)
                .select_related('artwork')
                .prefetch_related('artwork__artists')
                .order_by('-created_at')
            )
        return context


class ArtistUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Artist
    form_class = ArtistForm
    template_name = 'gallery/artist_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        artist = self.object
        if artist.user == self.request.user:
            is_empty = (
                not artist.image
                and not (artist.bio or '').strip()
                and not (artist.statement or '').strip()
                and not artist.artworks.exists()
            )
            context['show_claim_hint'] = is_empty
        return context

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
        return can_delete_artist(self.request.user, obj)


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
