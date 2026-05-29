from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from gallery.forms import ArtworkSubmissionForm
from gallery.models import Artist, Artwork, ArtworkSubmission, Show, ShowArtworkNumber
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




def _send_submission_confirmation(submission, request):
    if not submission.submitted_by:
        return
    email = submission.submitted_by.email
    if not email:
        return
    show = submission.show
    html = render_to_string('email/artwork_submission_confirmation.html', {
        'submission': submission,
        'show': show,
        'artwork': submission.artwork,
        'show_url': request.build_absolute_uri(show.get_absolute_url()),
    })
    send_mail(
        subject=f'Submission received: {submission.artwork.name} → {show.name}',
        message=f'Your artwork "{submission.artwork.name}" has been submitted to {show.name}.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html,
        fail_silently=True,
    )


def send_juror_review_notifications(show, request):
    """Email all assigned jurors when a show transitions to in_review."""
    from reviews.models import ShowJuror
    from gallery.models import ArtworkSubmission
    submission_count = ArtworkSubmission.objects.filter(show=show).count()
    dashboard_url = request.build_absolute_uri(
        reverse('reviews:show_review_dashboard', kwargs={'show_slug': show.slug})
    )
    for assignment in ShowJuror.objects.filter(show=show).select_related('user'):
        email = assignment.user.email
        if not email:
            continue
        html = render_to_string('email/juror_review_notification.html', {
            'show': show,
            'submission_count': submission_count,
            'dashboard_url': dashboard_url,
        })
        send_mail(
            subject=f'Jury review open: {show.name}',
            message=f'Jury review for {show.name} is now open. Please review {submission_count} submission(s) at {dashboard_url}',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=True,
        )


def send_submission_emails(show):
    """Send acceptance/rejection emails to all submitters. Called when a show is published."""
    subs = (
        ArtworkSubmission.objects.filter(
            show=show, status__in=[ArtworkSubmission.ACCEPTED, ArtworkSubmission.REJECTED]
        ).select_related('artwork', 'submitted_by')
    )
    for sub in subs:
        _send_selection_email(sub, accepted=(sub.status == ArtworkSubmission.ACCEPTED))


@login_required
def artwork_submit(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not show.is_accepting_submissions:
        return redirect(show)

    artist = request.user.artists.order_by('-created_at').first()
    if not artist:
        return redirect(show)

    already_submitted_ids = ArtworkSubmission.objects.filter(show=show).values_list('artwork_id', flat=True)
    available_artworks = (
        artist.artworks
        .exclude(pk__in=already_submitted_ids)
        .prefetch_related('artists')
        .order_by('name')
    )

    if request.method == 'POST':
        form = ArtworkSubmissionForm(request.POST, show=show, artist=artist)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.show = show
            submission.submitted_by = request.user
            submission.save()
            messages.success(request, f'"{submission.artwork.name}" has been submitted to {show.name}.')
            _send_submission_confirmation(submission, request)
            return redirect(show)
    else:
        form = ArtworkSubmissionForm(show=show, artist=artist)

    return render(request, 'gallery/artwork_submit.html', {
        'show': show,
        'form': form,
        'available_artworks': available_artworks,
        'has_any_artworks': artist.artworks.exists(),
    })


@login_required
def show_submissions(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_view_reviews(request.user, show):
        raise Http404

    query = request.GET.get('q', '').strip()
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
    if query:
        submissions = submissions.filter(
            Q(artwork__name__icontains=query) |
            Q(artwork__artists__first_name__icontains=query) |
            Q(artwork__artists__last_name__icontains=query)
        ).distinct()

    submissions = list(submissions)
    context = {
        'show': show,
        'submissions': submissions,
        'selected_submissions': [s for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_SELECTED],
        'rejected_submissions': [s for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_REJECTED],
        'n_selected': sum(1 for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_SELECTED),
        'n_rejected': sum(1 for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_REJECTED),
        'n_undecided': sum(1 for s in submissions if s.curator_decision == ArtworkSubmission.UNDECIDED),
        'can_manage': can_manage_show(request.user, show),
    }
    return render(request, 'gallery/show_submissions.html', context)


@login_required
def update_submission_status(request, pk):
    submission = get_object_or_404(ArtworkSubmission, pk=pk)
    if not can_manage_show(request.user, submission.show):
        raise Http404
    if request.method == 'POST':
        new_decision = request.POST.get('decision')
        if new_decision in {ArtworkSubmission.UNDECIDED, ArtworkSubmission.CURATOR_SELECTED, ArtworkSubmission.CURATOR_REJECTED}:
            submission.curator_decision = new_decision
            submission.save(update_fields=['curator_decision'])
    return redirect('gallery:show_submissions', slug=submission.show.slug)


@login_required
def promote_artworks(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    selected_subs = list(
        ArtworkSubmission.objects.filter(show=show, curator_decision=ArtworkSubmission.CURATOR_SELECTED)
        .select_related('artwork', 'submitted_by')
        .prefetch_related('artwork__artists')
        .order_by('submitted_at')
    )
    rejected_subs = list(
        ArtworkSubmission.objects.filter(show=show, curator_decision=ArtworkSubmission.CURATOR_REJECTED)
        .select_related('artwork', 'submitted_by')
        .prefetch_related('artwork__artists')
    )

    current_artwork_ids = set(show.artworks.values_list('id', flat=True))
    to_add = [s for s in selected_subs if s.artwork_id not in current_artwork_ids]
    to_keep = [s for s in selected_subs if s.artwork_id in current_artwork_ids]
    to_remove = [s for s in rejected_subs if s.artwork_id in current_artwork_ids]

    if request.method == 'POST':
        selected_artworks = [s.artwork for s in selected_subs]
        if selected_artworks:
            show.artworks.add(*selected_artworks)
            artist_ids = []
            for artwork in selected_artworks:
                artist_ids.extend(artwork.artists.values_list('id', flat=True))
            if artist_ids:
                show.artists.add(*Artist.objects.filter(id__in=artist_ids).distinct())
            existing_ids = set(
                ShowArtworkNumber.objects.filter(show=show).values_list('artwork_id', flat=True)
            )
            next_number = (
                ShowArtworkNumber.objects.filter(show=show).order_by('-number').values_list('number', flat=True).first() or 0
            ) + 1
            for artwork in selected_artworks:
                if artwork.id not in existing_ids:
                    ShowArtworkNumber.objects.create(show=show, artwork=artwork, number=next_number)
                    next_number += 1

        if to_remove:
            remove_artworks = [s.artwork for s in to_remove]
            show.artworks.remove(*remove_artworks)
            ShowArtworkNumber.objects.filter(show=show, artwork__in=remove_artworks).delete()

        # Publish decisions: update artist-visible status
        for sub in selected_subs:
            sub.status = ArtworkSubmission.ACCEPTED
            sub.save(update_fields=['status'])
        for sub in rejected_subs:
            sub.status = ArtworkSubmission.REJECTED
            sub.save(update_fields=['status'])

        return redirect(show)

    context = {
        'show': show,
        'to_add': to_add,
        'to_keep': to_keep,
        'to_remove': to_remove,
        'selected_submissions': selected_subs,
        'rejected_submissions': rejected_subs,
    }
    return render(request, 'gallery/promote_artworks.html', context)


@login_required
def renumber_artworks(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    if request.method == 'POST':
        ShowArtworkNumber.objects.filter(show=show).delete()
        subs = (
            ArtworkSubmission.objects
            .filter(show=show, curator_decision=ArtworkSubmission.CURATOR_SELECTED)
            .select_related('artwork')
            .order_by('submitted_at')
        )
        for number, sub in enumerate(subs, start=1):
            ShowArtworkNumber.objects.create(show=show, artwork=sub.artwork, number=number)
        messages.success(request, 'Artwork numbers have been reassigned.')
    return redirect(show)


@login_required
def retract_submission(request, pk):
    submission = get_object_or_404(ArtworkSubmission, pk=pk, submitted_by=request.user)
    if not submission.show.is_accepting_submissions:
        raise Http404
    if request.method == 'POST':
        submission.delete()
    return redirect('gallery:artist_open_call')
