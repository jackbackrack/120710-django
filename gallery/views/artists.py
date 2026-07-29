from eatart.schemaorg.mappers import artist_to_schema

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
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
from gallery.submission_cta import submit_ctas
from gallery.views.mixins import (CanonicalSlugRedirectMixin, StructuredDataMixin,
                                 visible_site_or_404)


class ArtistListView(ListView):
    """Every artist, or — at /site/<slug>/artists/ — those who have shown at one venue.

    The site-scoped form reuses this view and template for the same reason ShowListView
    does: the scoped list used to be a separate view over a 15-line template, and it had
    silently drifted into a thinner page with no pagination (it rendered every artist at
    the venue in one response), no tag filter, no count, no per-card permissions and no
    anonymous fragment cache.
    """
    model = Artist
    template_name = 'gallery/artist_list.html'
    paginate_by = 48
    site = None

    def get_template_names(self):
        if self.request.GET.get('partial'):
            return ['gallery/_artist_cards.html']
        return [self.template_name]

    def get_queryset(self):
        queryset = Artist.objects.filter(visible_artist_queryset(self.request.user)).prefetch_related('tags')
        site_slug = self.kwargs.get('site_slug')
        if site_slug:
            self.site = visible_site_or_404(self.request, site_slug)
            # Artist has no link to Site — the relation runs through the work: an artist
            # belongs to a venue because something of theirs was in a show there.
            queryset = queryset.filter(artworks__shows__sites=self.site)
        return tag_filter_queryset(queryset, self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        artists = list(context['object_list'])
        context['artist_list'] = artists
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        context['can_manage_artist'] = {a.id for a in artists if can_manage_artist(self.request.user, a)}
        context['can_delete_artist'] = {a.id for a in artists if can_delete_artist(self.request.user, a)}
        # Mirrors ArtistCreateView.test_func: your own profile if you have none, or a
        # record for another artist if you are a curator or staff.
        user = self.request.user
        context['can_create_artist'] = bool(
            user.is_authenticated
            and (not user.artists.exists()
                 or is_curator_user(user)
                 or is_staff_user(user)))
        context['site'] = self.site
        context['anon_grid_cache_seconds'] = settings.ANON_GRID_CACHE_SECONDS
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
        # Previously a missing photo bounced you into the editor every time you
        # looked at your own profile — an inescapable loop once the photo stopped
        # being required. The profile page now just nudges instead.
        if (request.user.is_authenticated
                and self.object.user == request.user
                and not self.object.first_name and not self.object.last_name):
            return redirect(reverse('gallery:artist_edit', kwargs={'pk': self.object.pk}))
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        from gallery.permissions import can_delete_artist, can_delete_artwork, can_manage_artist, can_manage_artwork, is_curator_user, visible_show_queryset, invited_show_ids
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
            inv_ids = invited_show_ids(user)   # matches by any of the user's emails or artist link
            submittable_shows = []
            for show in open_call_shows:
                if show.submission_type == Show.SUBMISSION_OPEN:
                    submittable_shows.append(show)
                elif show.submission_type == Show.SUBMISSION_INVITED and show.id in inv_ids:
                    submittable_shows.append(show)
            context['submittable_shows'] = submittable_shows
            # Submit stays offered even with an incomplete profile. Withholding it
            # left an artist looking at shows they could not act on, above a banner
            # telling them to go and fill in a form first — the "do homework, then
            # come back" shape that made this flow hard to follow. Starting a
            # submission asks for whatever is missing at that moment and returns
            # here, so the profile is completed inside the flow instead of ahead of it.
            context['submittable_show_ids'] = {s.id for s in submittable_shows}
        from gallery.permissions import can_delete_show, can_manage_show
        shows_qs = Show.objects.filter(artworks__artists=artist).prefetch_related('curators', 'tags', 'events').distinct()
        shows_qs = visible_show_queryset(shows_qs, user)
        shows = list(shows_qs.order_by('name'))
        context['shows'] = shows
        context['submit_ctas'] = submit_ctas(
            self.request, list(shows) + list(context.get('submittable_shows') or []))
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
        # Always show the artist's pinned artworks; for non-owners filter by visibility.
        if artist.user:
            from gallery.models import Artwork
            saved_qs = (
                SavedArtwork.objects
                .filter(user=artist.user)
                .select_related('artwork')
                .prefetch_related('artwork__artists')
                .order_by('display_order', '-created_at')
            )
            if not (user.is_authenticated and artist.user == user):
                saved_qs = saved_qs.filter(
                    artwork__in=Artwork.objects.filter(visible_artwork_queryset(user)).distinct()
                )
            context['saved_artworks'] = saved_qs
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
        # Mid-flow (arrived here on the way to submitting): show the tracker and
        # carry the destination across the POST.
        nxt = self.request.POST.get('next') or self.request.GET.get('next')
        if nxt and url_has_allowed_host_and_scheme(
                nxt, allowed_hosts={self.request.get_host()},
                require_https=self.request.is_secure()):
            context['next_url'] = nxt
            context['progress_step'] = 2
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

    def get_success_url(self):
        # Return to wherever the artist was headed (usually a show's submit page)
        # rather than dropping them on their own profile with no way back.
        nxt = self.request.POST.get('next') or self.request.GET.get('next')
        if nxt and url_has_allowed_host_and_scheme(
                nxt, allowed_hosts={self.request.get_host()},
                require_https=self.request.is_secure()):
            return nxt
        return super().get_success_url()

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
        # Only claim the new profile for its creator when they have none of their own.
        # A curator adding a record for someone who cannot sign up must not have their
        # own account silently attached to it — and for staff the form has a "Linked user
        # account" field, whose value (including blank) this would otherwise overwrite.
        if not self.request.user.artists.exists():
            form.instance.user = self.request.user
        return super().form_valid(form)

    def test_func(self):
        # Two separate reasons to be here: you have no profile and are creating your own,
        # or you are a curator/staff member creating a record for an artist who has no
        # account — someone a caregiver acts for, or anyone being added to an
        # invitation-only show directly. Curators used to be locked out of the second
        # case entirely, which also made the "Create the artist profile first" link on
        # the add-artwork-on-behalf page 403 for exactly the people it was aimed at.
        user = self.request.user
        return (not user.artists.exists()
                or is_curator_user(user)
                or is_staff_user(user))
