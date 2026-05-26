from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.mail import send_mail
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.generic import TemplateView

from gallery.forms import ArtworkSubmissionForm
from gallery.models import Artist, Artwork, ArtworkSubmission, Show
from gallery.permissions import can_manage_show, can_view_reviews, is_curator_user


def _send_selection_email(submission, accepted):
    email = submission.submitted_by.email
    if not email:
        return
    template = 'email/artwork_selected.html' if accepted else 'email/artwork_not_selected.html'
    subject = (
        f'Your work has been selected for {submission.show.name}'
        if accepted else
        f'Update on your submission to {submission.show.name}'
    )
    html = render_to_string(template, {
        'submission': submission,
        'show': submission.show,
        'artwork': submission.artwork,
    })
    send_mail(
        subject=subject,
        message=subject,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html,
        fail_silently=True,
    )


class OpenCallDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'gallery/open_call_dashboard.html'

    def test_func(self):
        return is_curator_user(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()

        shows = (
            Show.objects.filter(is_open_call=True)
            .select_related('managing_curator')
            .prefetch_related('tags')
            .annotate(submission_count=Count('submissions', distinct=True))
            .order_by('-start')
        )
        artworks = (
            Artwork.objects.filter(submissions__isnull=False)
            .prefetch_related('artists', 'tags', 'shows')
            .order_by('-created_at')
            .distinct()
        )

        if query:
            shows = shows.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(artists__name__icontains=query)
            ).distinct()
            artworks = artworks.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(artists__name__icontains=query)
                | Q(medium__icontains=query)
            ).distinct()

        context['query'] = query
        context['open_call_shows'] = shows
        context['open_call_artworks'] = artworks
        return context


class ArtistOpenCallView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'gallery/open_call_artist.html'

    def test_func(self):
        return self.request.user.artists.exists()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        artist = self.request.user.artists.order_by('-created_at').first()
        query = self.request.GET.get('q', '').strip()

        shows = (
            Show.objects.filter(is_open_call=True)
            .select_related('managing_curator')
            .prefetch_related('tags')
            .order_by('-start')
        )

        if query:
            shows = shows.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(tags__name__icontains=query)
            ).distinct()

        submissions_by_show = {}
        for sub in ArtworkSubmission.objects.filter(
            submitted_by=self.request.user, show__is_open_call=True
        ).select_related('artwork', 'show'):
            submissions_by_show.setdefault(sub.show_id, []).append(sub)

        show_data = [
            {
                'show': show,
                'submissions': submissions_by_show.get(show.id, []),
                'can_submit': show.is_accepting_submissions,
            }
            for show in shows
        ]

        context['artist'] = artist
        context['query'] = query
        context['show_data'] = show_data
        return context


@login_required
def artwork_submit(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not show.is_accepting_submissions:
        return redirect(show)

    artist = request.user.artists.order_by('-created_at').first()
    if not artist:
        return redirect(show)

    if request.method == 'POST':
        form = ArtworkSubmissionForm(request.POST, show=show, artist=artist)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.show = show
            submission.submitted_by = request.user
            submission.save()
            return redirect(show)
    else:
        form = ArtworkSubmissionForm(show=show, artist=artist)

    return render(request, 'gallery/artwork_submit.html', {'show': show, 'form': form})


@login_required
def show_submissions(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_view_reviews(request.user, show):
        raise Http404

    submissions = (
        ArtworkSubmission.objects.filter(show=show)
        .select_related('artwork', 'submitted_by')
        .prefetch_related('artwork__artists')
        .annotate(
            avg_rating=Avg(
                'artwork__reviews__rating',
                filter=Q(artwork__reviews__show=show),
            ),
            review_count=Count(
                'artwork__reviews',
                filter=Q(artwork__reviews__show=show),
                distinct=True,
            ),
        )
        .order_by('-avg_rating', 'artwork__name')
    )

    if request.method == 'POST' and can_manage_show(request.user, show):
        for sub in ArtworkSubmission.objects.filter(show=show):
            new_status = request.POST.get(f'status_{sub.id}')
            if new_status in {ArtworkSubmission.SUBMITTED, ArtworkSubmission.SELECTED, ArtworkSubmission.REJECTED}:
                if sub.status != new_status:
                    sub.status = new_status
                    sub.save(update_fields=['status'])
        return redirect('gallery:show_submissions', slug=slug)

    context = {
        'show': show,
        'submissions': submissions,
        'can_manage': can_manage_show(request.user, show),
        'status_choices': ArtworkSubmission.STATUS_CHOICES,
    }
    return render(request, 'gallery/show_submissions.html', context)


@login_required
def promote_artworks(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    selected = list(
        ArtworkSubmission.objects.filter(show=show, status=ArtworkSubmission.SELECTED)
        .select_related('artwork', 'submitted_by')
        .prefetch_related('artwork__artists')
    )
    rejected = list(
        ArtworkSubmission.objects.filter(show=show, status=ArtworkSubmission.REJECTED)
        .select_related('artwork', 'submitted_by')
        .prefetch_related('artwork__artists')
    )

    if request.method == 'POST':
        selected_artworks = [s.artwork for s in selected]
        if selected_artworks:
            show.artworks.add(*selected_artworks)
            artist_ids = []
            for artwork in selected_artworks:
                artist_ids.extend(artwork.artists.values_list('id', flat=True))
            if artist_ids:
                show.artists.add(*Artist.objects.filter(id__in=artist_ids).distinct())

        for submission in selected:
            _send_selection_email(submission, accepted=True)
        for submission in rejected:
            _send_selection_email(submission, accepted=False)

        return redirect(show)

    context = {
        'show': show,
        'selected_submissions': selected,
        'rejected_submissions': rejected,
    }
    return render(request, 'gallery/promote_artworks.html', context)
