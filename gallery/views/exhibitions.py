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
from gallery.models import Artist, Artwork, ArtworkSubmission, Show, Tag
from gallery.permissions import can_delete_artist, can_delete_artwork, can_delete_show, can_manage_artist, can_manage_artwork, can_manage_show, can_view_reviews, is_staff_user, tag_filter_queryset, visible_artwork_queryset, visible_show_queryset
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin


class ShowListView(ListView):
    model = Show
    template_name = 'gallery/show_list.html'

    def get_queryset(self):
        qs = Show.objects.prefetch_related('curators', 'tags', 'events')
        qs = visible_show_queryset(qs, self.request.user)
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
        artworks = Artwork.objects.filter(shows=show).filter(visible_artwork_queryset(self.request.user)).prefetch_related('artists').annotate(first_artist_name=Min('artists__name')).order_by('first_artist_name', 'name').distinct()
        artists = Artist.objects.filter(artworks__in=artworks).distinct().order_by('name')
        context['artists'] = artists
        context['can_manage_artist_ids'] = {a.id for a in artists if can_manage_artist(self.request.user, a)}
        context['can_delete_artist_ids'] = {a.id for a in artists if can_delete_artist(self.request.user, a)}
        context['can_view_reviews'] = can_view_reviews(self.request.user, show)
        context['can_manage_show'] = can_manage_show(self.request.user, show)
        context['can_delete_show'] = can_delete_show(self.request.user, show)

        user = self.request.user
        context['can_submit'] = False
        context['has_invitation'] = False
        submissions_by_artwork_id = {}
        pending_submissions = []
        if user.is_authenticated:
            artist = user.artists.order_by('-created_at').first()
            if artist:
                if show.submission_type == Show.SUBMISSION_OPEN:
                    context['can_submit'] = show.is_accepting_submissions
                elif show.submission_type == Show.SUBMISSION_INVITED:
                    has_inv = show.invitations.filter(email__iexact=user.email).exists()
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
        return context


def redirect_to_latest_show(request):
    now = timezone.now()
    ongoing_shows = Show.objects.filter(start__lte=now, end__gte=now).order_by('-start')
    upcoming_shows = Show.objects.filter(start__gt=now).order_by('start')
    current_show = ongoing_shows.first()

    if current_show:
        return redirect(current_show)

    next_show = upcoming_shows.first()
    if next_show:
        return redirect(next_show)

    return redirect('/shows/')


class ShowCatalogView(ShowDetailView):
    canonical_url_name = 'gallery:show_catalog'
    template_name = 'gallery/show_catalog.html'


class ShowPlacardsView(CanonicalSlugRedirectMixin, DetailView):
    model = Show
    canonical_url_name = 'gallery:show_placards_detail'
    template_name = 'gallery/show_placards_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show = kwargs.get('object')
        context['artworks'] = Artwork.objects.filter(shows=show).filter(visible_artwork_queryset(self.request.user)).distinct().order_by('artists__name')
        return context


class ShowInstagramView(CanonicalSlugRedirectMixin, DetailView):
    model = Show
    canonical_url_name = 'gallery:show_instagram_detail'
    template_name = 'gallery/show_instagram_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show = kwargs.get('object')
        context['artworks'] = Artwork.objects.filter(shows=show).filter(visible_artwork_queryset(self.request.user)).order_by('artists__name', 'name').distinct()
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
        return is_staff_user(self.request.user)


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
        messages.success(request, f'Status changed to {show.get_status_display()}.')
    return redirect(show)
