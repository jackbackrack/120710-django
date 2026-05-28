import datetime

from eatart.schemaorg.mappers import show_to_schema

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Min, Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from gallery.forms import ShowForm
from gallery.models import Artist, Artwork, ArtworkSubmission, Show, Tag
from gallery.permissions import can_manage_show, can_see_all_shows, can_view_reviews, is_staff_user, tag_filter_queryset, visible_artwork_queryset
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin


class ShowListView(ListView):
    model = Show
    template_name = 'gallery/show_list.html'

    def get_queryset(self):
        qs = Show.objects.prefetch_related('curators', 'tags')
        if not can_see_all_shows(self.request.user):
            qs = qs.filter(status__in=Show.PUBLIC_STATUSES)
        return tag_filter_queryset(qs, self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = datetime.date.today()
        base = self.get_queryset()
        all_shows = list(base)
        context['current_shows'] = base.filter(start__lte=today, end__gte=today).order_by('-start')
        context['future_shows'] = base.filter(start__gt=today).order_by('start')
        context['past_shows'] = base.filter(end__lt=today).order_by('-start')
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        context['can_manage_show'] = {s.id for s in all_shows if can_manage_show(self.request.user, s)}
        return context


class ShowDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Show
    schema_mapper = show_to_schema
    template_name = 'gallery/show_detail.html'

    def get_queryset(self):
        qs = Show.objects.all()
        if not can_see_all_shows(self.request.user):
            qs = qs.filter(status__in=Show.PUBLIC_STATUSES)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show = kwargs.get('object')
        artworks = Artwork.objects.filter(shows=show).filter(visible_artwork_queryset(self.request.user)).annotate(first_artist_name=Min('artists__name')).order_by('first_artist_name', 'name').distinct()
        artists = Artist.objects.filter(Q(shows=show) | Q(artworks__in=artworks)).distinct().order_by('name')
        context['artists'] = artists
        context['can_view_reviews'] = can_view_reviews(self.request.user, show)
        context['can_manage_show'] = can_manage_show(self.request.user, show)

        user = self.request.user
        context['can_submit'] = False
        submissions_by_artwork_id = {}
        pending_submissions = []
        if show.is_open_call and user.is_authenticated:
            artist = user.artists.order_by('-created_at').first()
            if artist:
                context['can_submit'] = show.is_accepting_submissions
                subs = list(
                    ArtworkSubmission.objects.filter(show=show, submitted_by=user)
                    .select_related('artwork')
                    .prefetch_related('artwork__artists')
                )
                submissions_by_artwork_id = {sub.artwork_id: sub for sub in subs}
                artwork_ids_in_show = set(artworks.values_list('id', flat=True))
                pending_submissions = [sub for sub in subs if sub.artwork_id not in artwork_ids_in_show]

        context['artwork_data'] = [
            {'artwork': aw, 'submission': submissions_by_artwork_id.get(aw.id)}
            for aw in artworks
        ]
        context['pending_submissions'] = pending_submissions
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
        return can_manage_show(self.request.user, obj)


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
