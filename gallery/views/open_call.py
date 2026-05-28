from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from gallery.forms import ArtworkSubmissionForm
from gallery.models import Artist, Artwork, ArtworkSubmission, Show
from gallery.permissions import can_manage_show, can_view_reviews, is_curator_user


def _send_selection_email(submission, accepted):
    if not submission.submitted_by:
        return
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




def send_submission_emails(show):
    """Send acceptance/rejection emails to all submitters. Called when a show is published."""
    subs = (
        ArtworkSubmission.objects.filter(
            show=show, status__in=[ArtworkSubmission.SELECTED, ArtworkSubmission.REJECTED]
        ).select_related('artwork', 'submitted_by')
    )
    for sub in subs:
        _send_selection_email(sub, accepted=(sub.status == ArtworkSubmission.SELECTED))


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

        return redirect(show)

    context = {
        'show': show,
        'selected_submissions': selected,
        'rejected_submissions': rejected,
    }
    return render(request, 'gallery/promote_artworks.html', context)


@login_required
def retract_submission(request, pk):
    submission = get_object_or_404(ArtworkSubmission, pk=pk, submitted_by=request.user)
    if not submission.show.is_accepting_submissions:
        raise Http404
    if request.method == 'POST':
        submission.delete()
    return redirect('gallery:artist_open_call')
