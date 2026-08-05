import datetime

from eatart.schemaorg.mappers import show_to_schema

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Min
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from gallery.forms import ShowForm
from gallery.models import Artist, Artwork, ArtworkSubmission, Show, Site, Tag
from gallery.models.show_artwork_numbers import ShowArtworkNumber
from gallery.permissions import can_delete_artist, can_delete_artwork, can_delete_show, can_manage_artist, can_manage_artwork, can_manage_show, can_view_reviews, is_staff_user, tag_filter_queryset, visible_artwork_queryset, visible_show_queryset, is_site_director
from gallery.show_actions import show_actions
from gallery.submission_cta import submit_cta, submit_ctas
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin
# Cards per Avery 5376 sheet — shown on the picker so a curator can see how many
# sheets a selection will need.
from gallery.views.placards import PER_PAGE as PLACARDS_PER_PAGE



def _needs_consignment(user, show):
    """Whether to offer this viewer the consignment button on a show page.

    Only their own agreement, and only when there is something to do: they have work in the
    show, they are not represented by a gallery that consigns on their behalf, and they have
    not already signed a current version. A button that leads to "nothing to do here" is
    worse than no button.
    """
    if not user.is_authenticated:
        return False
    artist = Artist.objects.filter(user=user, artworks__shows=show).distinct().first()
    if artist is None or artist.is_represented:
        return False

    from gallery import consignment as terms
    from gallery.models import Consignment

    # No rate, no agreement — so no button. Without this an artist gets a prominent
    # invitation to sign that leads to "this is not ready yet", which is worse than no
    # button at all, and it is the state every venue is in until somebody sets a rate.
    try:
        terms.commission_rate_for(show)
    except terms.NoCommissionRate:
        return False

    signed = (Consignment.objects
              .filter(show=show, artist=artist, status=Consignment.STATUS_SIGNED)
              .order_by('-version').first())
    if signed is None:
        return True
    return terms.is_out_of_date(signed)

class ShowListView(ListView):
    """All shows, or — at /site/<slug>/shows/ — just one venue's.

    The site-scoped form reuses this view and its template so a venue's list is the
    same page with the same controls (New, Slideshow, tag filter, submit buttons)
    rather than a thinner copy that drifts.
    """
    model = Show
    template_name = 'gallery/show_list.html'
    site = None

    def get_queryset(self):
        qs = Show.objects.prefetch_related('curators', 'tags', 'events', 'sites')
        qs = visible_show_queryset(qs, self.request.user)
        site_slug = self.kwargs.get('site_slug')
        if site_slug:
            self.site = get_object_or_404(Site, slug=site_slug)
            qs = qs.filter(sites=self.site)
        return tag_filter_queryset(qs, self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = datetime.date.today()
        all_shows = list(context['object_list'])
        context['current_shows'] = [s for s in all_shows if s.start <= today <= s.end]
        context['future_shows'] = sorted([s for s in all_shows if s.start > today], key=lambda s: s.start)
        context['past_shows'] = sorted([s for s in all_shows if s.end < today], key=lambda s: s.start, reverse=True)
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        context['can_manage_show'] = {s.id for s in all_shows if can_manage_show(self.request.user, s)}
        context['can_delete_show'] = {s.id for s in all_shows if can_delete_show(self.request.user, s)}
        context['submit_ctas'] = submit_ctas(self.request, all_shows)
        context['site'] = self.site
        return context


class ShowDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Show
    schema_mapper = show_to_schema
    template_name = 'gallery/show_detail.html'

    def get_queryset(self):
        qs = Show.objects.prefetch_related(
            'curators',
            'artworks__artists',
            'artworks__shows',
        )
        return visible_show_queryset(qs, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show = kwargs.get('object')
        artworks = Artwork.objects.filter(shows=show).filter(visible_artwork_queryset(self.request.user)).prefetch_related('artists', 'shows', 'shows__curators').annotate(first_artist_name=Min('artists__name')).order_by('first_artist_name', 'name').distinct()
        artists = Artist.objects.filter(artworks__in=artworks).distinct().order_by('name')
        context['artists'] = artists
        context['can_manage_artist_ids'] = {a.id for a in artists if can_manage_artist(self.request.user, a)}
        context['can_delete_artist_ids'] = {a.id for a in artists if can_delete_artist(self.request.user, a)}
        context['can_view_reviews'] = can_view_reviews(self.request.user, show)
        context['can_manage_show'] = can_manage_show(self.request.user, show)
        # Artist may schedule drop-off/pickup if they have work in the show and
        # the curator has defined any windows.
        context['can_schedule_dropoff'] = (
            self.request.user.is_authenticated
            and show.schedule_windows.exists()
            and Artist.objects.filter(user=self.request.user, artworks__shows=show).exists()
        )
        context['has_schedule_windows'] = show.schedule_windows.exists()
        # A button of their own, not only the emailed link. An artist who has heard the
        # gallery needs an agreement should be able to go and find it, and the email may be
        # in a spam folder or sent before they had work in the show.
        context['needs_consignment'] = _needs_consignment(self.request.user, show)
        context['can_delete_show'] = can_delete_show(self.request.user, show)
        has_placements = show.wall_placements.exists()
        context['has_placements'] = has_placements
        published = show.status in (Show.STATUS_PUBLISHED, Show.STATUS_CLOSED)
        context['can_view_3d'] = has_placements and (published or can_manage_show(self.request.user, show))
        # The web checklist needs no placements — just a show anyone may see.
        context['can_view_checklist'] = published or can_manage_show(self.request.user, show)
        if can_manage_show(self.request.user, show) and published:
            context['emails_pending'] = ArtworkSubmission.objects.filter(
                show=show,
                status__in=[ArtworkSubmission.ACCEPTED, ArtworkSubmission.REJECTED],
                email_sent_at__isnull=True,
            ).count()
            context['emails_sent'] = ArtworkSubmission.objects.filter(
                show=show,
                email_sent_at__isnull=False,
            ).count()
        else:
            context['emails_pending'] = 0
            context['emails_sent'] = 0

        user = self.request.user
        context['can_submit'] = False
        context['has_invitation'] = False
        submissions_by_artwork_id = {}
        pending_submissions = []
        artist_profile = None
        if user.is_authenticated:
            artist = user.artists.order_by('-created_at').first()
            artist_profile = artist
            if artist:
                if show.submission_type == Show.SUBMISSION_OPEN:
                    context['can_submit'] = show.is_accepting_submissions
                elif show.submission_type == Show.SUBMISSION_INVITED:
                    from gallery.permissions import user_invited_to_show
                    has_inv = user_invited_to_show(show, user)
                    context['has_invitation'] = has_inv
                    context['can_submit'] = show.is_accepting_submissions and has_inv
                subs = list(
                    ArtworkSubmission.objects.filter(show=show, submitted_by=user)
                    .select_related('artwork')
                    .prefetch_related('artwork__artists')
                )
                submissions_by_artwork_id = {sub.artwork_id: sub for sub in subs}
                artwork_ids_in_show = set(artworks.values_list('id', flat=True))
                hide_rejected = show.status in (Show.STATUS_PUBLISHED, Show.STATUS_CLOSED)
                pending_submissions = [
                    sub for sub in subs
                    if sub.artwork_id not in artwork_ids_in_show
                    and not (hide_rejected and sub.status == ArtworkSubmission.REJECTED)
                ]

        context['artwork_data'] = [
            {'artwork': aw, 'submission': submissions_by_artwork_id.get(aw.id)}
            for aw in artworks
        ]
        context['can_manage_artwork_ids'] = {
            aw.id for aw in artworks if can_manage_artwork(user, aw)
        }
        context['can_delete_artwork_ids'] = {
            aw.id for aw in artworks if can_delete_artwork(user, aw)
        }
        context['pending_submissions'] = pending_submissions
        context['submit_cta'] = submit_cta(self.request, show,
                                           artist=artist_profile, artist_loaded=True)
        from reviews.models import ShowJuror
        context['jurors'] = list(ShowJuror.objects.filter(show=show).select_related('user').order_by('user__last_name'))
        context['rubric_criteria_count'] = show.rubric_criteria.count()
        allowed = show.get_valid_transitions().get(show.status, [])
        status_choices = dict(Show.STATUS_CHOICES)
        context['allowed_transitions'] = [(s, status_choices[s]) for s in allowed]
        late_statuses = {Show.STATUS_DRAFT, Show.STATUS_PUBLISHED, Show.STATUS_CLOSED}
        context['can_assign_jurors'] = can_manage_show(user, show) and show.status not in late_statuses
        from gallery.permissions import _is_gallery_admin
        print_statuses = {Show.STATUS_PUBLISHED, Show.STATUS_CLOSED}
        context['can_show_print_controls'] = can_manage_show(user, show) and (
            user.is_superuser or _is_gallery_admin(user) or show.status in print_statuses
        )
        context['show_actions'] = show_actions(
            show,
            can_manage=context['can_manage_show'],
            can_delete=context['can_delete_show'],
            can_view_reviews=context['can_view_reviews'],
            can_assign_jurors=context.get('can_assign_jurors', False),
            can_view_3d=context['can_view_3d'],
            can_view_checklist=context['can_view_checklist'],
            can_schedule_dropoff=context['can_schedule_dropoff'],
            needs_consignment=context['needs_consignment'],
            can_print_controls=context.get('can_show_print_controls', False),
            emails_pending=context.get('emails_pending', 0),
            emails_sent=context.get('emails_sent', 0),
        )
        return context


def redirect_to_latest_show(request, site_slug=None, target='detail'):
    """Redirect to the show running now, else the next one starting.

    target='checklist' lands on that show's web checklist instead of its detail page,
    so a site can publish one durable "current checklist" link.

    With site_slug (/site/<site>/show/latest) the search is limited to that site's
    shows and the redirect stays inside the site's URL space, so a visitor following
    a site link is not dropped out of it. Falls back to the site's page, or the
    global show list when unscoped.

    Only shows the viewer may actually open are considered: ShowDetailView filters by
    visible_show_queryset, so redirecting to a draft would land an anonymous visitor
    on a 404. A curator still gets their own unpublished shows.
    """
    now = timezone.now()
    shows = visible_show_queryset(Show.objects.all(), request.user)

    site = None
    if site_slug is not None:
        from gallery.models import Site
        site = get_object_or_404(Site, slug=site_slug)
        shows = shows.filter(sites=site)
    shows = shows.distinct()

    show = (shows.filter(start__lte=now, end__gte=now).order_by('-start').first()
            or shows.filter(start__gt=now).order_by('start').first())

    if show is None:
        return redirect(site.get_absolute_url() if site else '/shows/')
    if target == 'checklist':
        # The checklist page has no site-scoped URL — it reads the show's own site —
        # so both the scoped and unscoped routes land on the same place.
        return redirect('gallery:show_checklist', slug=show.slug)
    if site is not None:
        return redirect('gallery:site_show_detail', site_slug=site.slug, slug=show.slug)
    return redirect(show)


class ShowCatalogView(ShowDetailView):
    canonical_url_name = 'gallery:show_catalog'
    template_name = 'gallery/show_catalog.html'


class ShowPlacardsView(CanonicalSlugRedirectMixin, DetailView):
    """Placard list for a show, and — for curators — a picker that builds a PDF of
    just the selected cards. The page itself stays public (PublicUrlTests asserts
    that); only the print form is gated, matching the PDF endpoint it submits to."""
    model = Show
    canonical_url_name = 'gallery:show_placards_detail'
    template_name = 'gallery/show_placards_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show = kwargs.get('object')
        artworks = list(
            Artwork.objects.filter(shows=show)
            .filter(visible_artwork_queryset(self.request.user))
            .prefetch_related('artists').distinct()
        )
        # Same order the sheet is laid out in (placard number, then title), so the
        # list on screen reads in the order the cards will come off the printer.
        numbers = {sn.artwork_id: sn.number
                   for sn in ShowArtworkNumber.objects.filter(show=show)}
        artworks.sort(key=lambda a: (numbers.get(a.id, 10 ** 9), (a.name or '').lower()))
        for a in artworks:
            a.placard_number = numbers.get(a.id)   # None → unnumbered, sorts last
        context['artworks'] = artworks
        context['per_page'] = PLACARDS_PER_PAGE
        context['can_manage_show'] = can_manage_show(self.request.user, show)
        return context


class ShowInstagramView(CanonicalSlugRedirectMixin, DetailView):
    model = Show
    canonical_url_name = 'gallery:show_instagram_detail'
    template_name = 'gallery/show_instagram_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show = kwargs.get('object')
        context['artworks'] = Artwork.objects.filter(shows=show).filter(visible_artwork_queryset(self.request.user)).prefetch_related('artists').order_by('artists__name', 'name').distinct()
        return context


class ShowRubricView(CanonicalSlugRedirectMixin, DetailView):
    model = Show
    template_name = 'gallery/show_rubric.html'
    canonical_url_name = 'gallery:show_rubric'

    def get_queryset(self):
        qs = Show.objects.all()
        return visible_show_queryset(qs, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['criteria'] = self.object.rubric_criteria.all()
        return context


class ShowUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Show
    form_class = ShowForm
    template_name = 'gallery/show_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def test_func(self):
        obj = self.get_object()
        return can_manage_show(self.request.user, obj)

    def form_valid(self, form):
        old_status = Show.objects.values_list('status', flat=True).get(pk=form.instance.pk)
        response = super().form_valid(form)
        new_status = self.object.status
        if old_status != Show.STATUS_PUBLISHED and new_status == Show.STATUS_PUBLISHED:
            from gallery.views.open_call import send_submission_emails
            send_submission_emails(self.object)
        if old_status != Show.STATUS_IN_REVIEW and new_status == Show.STATUS_IN_REVIEW:
            from gallery.views.open_call import send_juror_review_notifications
            send_juror_review_notifications(self.object, self.request)
        return response


class ShowDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Show
    template_name = 'gallery/show_delete.html'
    success_url = reverse_lazy('gallery:show_list')

    def test_func(self):
        obj = self.get_object()
        return can_delete_show(self.request.user, obj)


class ShowCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Show
    form_class = ShowForm
    template_name = 'gallery/show_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def test_func(self):
        # A director creates shows for their own venue; the form's site choices are
        # narrowed to theirs, so there is nowhere else to put one.
        return is_staff_user(self.request.user) or is_site_director(self.request.user)


@login_required
def transition_show_status(request, pk):
    show = get_object_or_404(Show, pk=pk)
    if not can_manage_show(request.user, show):
        raise Http404
    if request.method == 'POST':
        new_status = request.POST.get('status')
        allowed = show.get_valid_transitions().get(show.status, [])
        if new_status not in allowed:
            messages.error(request, 'Invalid status transition.')
            return redirect(show)
        # Draft→Published always goes through promote/publish page
        if new_status == Show.STATUS_PUBLISHED:
            return redirect('gallery:promote_artworks', slug=show.slug)
        old_status = show.status
        show.status = new_status
        show.save(update_fields=['status'])
        if old_status != Show.STATUS_IN_REVIEW and new_status == Show.STATUS_IN_REVIEW:
            from gallery.views.open_call import send_juror_review_notifications
            send_juror_review_notifications(show, request)
        if new_status == Show.STATUS_DRAFT:
            from gallery.models import ArtworkSubmission
            selected_ids = list(
                ArtworkSubmission.objects.filter(
                    show=show, curator_decision=ArtworkSubmission.CURATOR_SELECTED
                ).values_list('artwork_id', flat=True)
            )
            if selected_ids:
                show.artworks.add(*selected_ids)
                messages.info(request, f'{len(selected_ids)} selected artwork(s) added to show for layout.')
        messages.success(request, f'Status changed to {show.get_status_display()}.')
    return redirect(show)
