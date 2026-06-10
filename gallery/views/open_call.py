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
from gallery.forms import ArtworkSubmissionForm, QuickArtworkForm
from gallery.models import Artist, Artwork, ArtworkSubmission, Show, ShowArtworkNumber, ShowInvitation
from gallery.permissions import can_manage_show, can_view_reviews
from reviews.views import _compute_weighted_scores


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


def _send_invitation_email(show, email, request):
    show_url = request.build_absolute_uri(show.get_absolute_url())
    signup_url = request.build_absolute_uri(reverse('account_signup'))
    html = render_to_string('email/show_invitation.html', {
        'show': show,
        'show_url': show_url,
        'signup_url': signup_url,
    })
    send_mail(
        subject=f'Invitation to submit artwork to {show.name}',
        message=(
            f'You have been invited to submit artwork to {show.name}. '
            f'Visit {show_url} to submit. '
            f'If you do not have an account, sign up at {signup_url}.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html,
        fail_silently=True,
    )


@login_required
def send_submission_reminders(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    if request.method != 'POST':
        return redirect('gallery:show_submissions', slug=slug)

    submitted_emails = set(
        ArtworkSubmission.objects.filter(show=show)
        .exclude(submitted_by__isnull=True)
        .values_list('submitted_by__email', flat=True)
    )
    submitted_emails = {e.lower() for e in submitted_emails if e}

    not_submitted = show.invitations.exclude(
        email__in=submitted_emails
    ).exclude(
        email__in=[e.upper() for e in submitted_emails]
    )
    # Case-insensitive exclusion via Python
    not_submitted = [inv for inv in show.invitations.all() if (inv.email or '').lower() not in submitted_emails]

    show_url = request.build_absolute_uri(show.get_absolute_url())
    count = 0
    for inv in not_submitted:
        html = render_to_string('email/show_submission_reminder.html', {
            'show': show,
            'show_url': show_url,
        })
        send_mail(
            subject=f'Reminder: submit your artwork to {show.name}',
            message=(
                f'This is a reminder to submit your artwork to {show.name}. '
                f'The show will be closing shortly. Visit {show_url} to submit.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[inv.email],
            html_message=html,
            fail_silently=True,
        )
        count += 1

    messages.success(request, f'Reminder sent to {count} artist{"s" if count != 1 else ""}.')
    return redirect('gallery:show_submissions', slug=slug)


@login_required
def invite_artists(request, slug):
    import re as _re
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    if show.submission_type != Show.SUBMISSION_INVITED:
        raise Http404

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            inv_pk = request.POST.get('invitation_pk')
            ShowInvitation.objects.filter(pk=inv_pk, show=show).delete()
            messages.success(request, 'Invitation removed.')
        else:
            raw = request.POST.get('emails', '')
            new_emails = {
                e.strip().lower() for e in _re.split(r'[\s,;]+', raw)
                if _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', e.strip())
            }
            existing_map = {inv.email.lower(): inv for inv in show.invitations.all()}
            existing_emails = set(existing_map)

            added = 0
            for email in sorted(new_emails - existing_emails):
                invitation = ShowInvitation.objects.create(
                    show=show, email=email, invited_by=request.user,
                )
                artist = (
                    Artist.objects.filter(user__email__iexact=email).first()
                    or Artist.objects.filter(email__iexact=email, user__isnull=True).first()
                )
                if artist:
                    invitation.artist = artist
                    invitation.save(update_fields=['artist'])
                _send_invitation_email(show, email, request)
                added += 1

            submitted_emails = {
                e.lower() for e in
                ArtworkSubmission.objects.filter(show=show)
                .values_list('submitted_by__email', flat=True)
                if e
            }
            removed = 0
            kept = []
            for email in existing_emails - new_emails:
                if email in submitted_emails:
                    kept.append(email)
                else:
                    ShowInvitation.objects.filter(show=show, email__iexact=email).delete()
                    removed += 1

            parts = []
            if added:
                parts.append(f'{added} invitation{"s" if added != 1 else ""} sent')
            if removed:
                parts.append(f'{removed} removed')
            if kept:
                parts.append(f'{len(kept)} kept (already submitted: {", ".join(kept)})')
            if parts:
                messages.success(request, ', '.join(parts).capitalize() + '.')
            else:
                messages.info(request, 'No changes.')

        return redirect('gallery:invite_artists', slug=slug)

    invitations = show.invitations.select_related('artist').order_by('email')
    current_emails = '\n'.join(inv.email for inv in invitations)
    return render(request, 'gallery/invite_artists.html', {
        'show': show,
        'invitations': invitations,
        'current_emails': current_emails,
    })


@login_required
def artwork_submit(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not show.is_accepting_submissions:
        return redirect(show)

    artist = request.user.artists.order_by('-created_at').first()
    if not artist:
        return redirect(show)

    if not artist.image:
        messages.error(request, 'Please complete your artist profile by adding a photo before submitting to a show.')
        return redirect(reverse('gallery:artist_edit', kwargs={'pk': artist.pk}))

    if show.submission_type == Show.SUBMISSION_INVITED:
        if not show.invitations.filter(email__iexact=request.user.email).exists():
            messages.error(request, 'Submissions to this show are by invitation only.')
            return redirect(show)

    already_submitted_ids = ArtworkSubmission.objects.filter(show=show).values_list('artwork_id', flat=True)
    available_artworks = (
        artist.artworks
        .exclude(pk__in=already_submitted_ids)
        .prefetch_related('artists')
        .order_by('name')
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_artwork':
            quick_form = QuickArtworkForm(request.POST, request.FILES)
            if quick_form.is_valid():
                artwork = quick_form.save(commit=False)
                artwork.created_by = request.user
                artwork.save()
                artwork.artists.add(artist)
                url = reverse('gallery:artwork_submit', kwargs={'slug': slug})
                return redirect(f'{url}?new_pk={artwork.pk}')
            form = ArtworkSubmissionForm(show=show, artist=artist)
            preseed_pk = None
        else:
            quick_form = QuickArtworkForm()
            form = ArtworkSubmissionForm(request.POST, show=show, artist=artist)
            if form.is_valid():
                submission = form.save(commit=False)
                submission.show = show
                submission.submitted_by = request.user
                submission.save()
                messages.success(request, f'"{submission.artwork.name}" has been submitted to {show.name}.')
                _send_submission_confirmation(submission, request)
                return redirect(show)
            preseed_pk = None
    else:
        form = ArtworkSubmissionForm(show=show, artist=artist)
        quick_form = QuickArtworkForm()
        preseed_pk = request.GET.get('new_pk')

    return render(request, 'gallery/artwork_submit.html', {
        'show': show,
        'form': form,
        'quick_form': quick_form,
        'available_artworks': available_artworks,
        'has_any_artworks': artist.artworks.exists(),
        'preseed_pk': preseed_pk,
        'show_quick_form': action == 'create_artwork' if request.method == 'POST' else False,
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
    criteria = list(show.rubric_criteria.all())
    weighted_scores = _compute_weighted_scores(show, criteria) if criteria else {}
    for sub in submissions:
        sub.weighted_score = weighted_scores.get(sub.artwork_id)

    # For invited shows, compute who has and hasn't submitted.
    invited_submitted = []
    invited_not_submitted = []
    if show.submission_type == Show.SUBMISSION_INVITED:
        from gallery.models import ShowInvitation
        invitations = list(
            show.invitations.select_related('artist').order_by('email')
        )
        submitted_emails = {
            (s.submitted_by.email or '').lower()
            for s in submissions
            if s.submitted_by_id
        }
        for inv in invitations:
            email = (inv.email or '').lower()
            artist = inv.artist
            if email in submitted_emails:
                invited_submitted.append(inv)
            else:
                invited_not_submitted.append(inv)

    context = {
        'show': show,
        'criteria': criteria,
        'submissions': submissions,
        'undecided_submissions': [s for s in submissions if s.curator_decision == ArtworkSubmission.UNDECIDED],
        'selected_submissions': [s for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_SELECTED],
        'rejected_submissions': [s for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_REJECTED],
        'n_selected': sum(1 for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_SELECTED),
        'n_rejected': sum(1 for s in submissions if s.curator_decision == ArtworkSubmission.CURATOR_REJECTED),
        'n_undecided': sum(1 for s in submissions if s.curator_decision == ArtworkSubmission.UNDECIDED),
        'can_manage': can_manage_show(request.user, show),
        'invited_submitted': invited_submitted,
        'invited_not_submitted': invited_not_submitted,
        'invited_total': len(invited_submitted) + len(invited_not_submitted),
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

    undecided_count = ArtworkSubmission.objects.filter(
        show=show, curator_decision=ArtworkSubmission.UNDECIDED
    ).count()

    current_artwork_ids = set(show.artworks.values_list('id', flat=True))
    to_add = [s for s in selected_subs if s.artwork_id not in current_artwork_ids]
    to_keep = [s for s in selected_subs if s.artwork_id in current_artwork_ids]
    to_remove = [s for s in rejected_subs if s.artwork_id in current_artwork_ids]
    to_reject = [s for s in rejected_subs if s.artwork_id not in current_artwork_ids]

    if request.method == 'POST':
        if undecided_count:
            messages.error(request, f'{undecided_count} submission{"s" if undecided_count != 1 else ""} still undecided. Decide all submissions before publishing.')
            return redirect('gallery:promote_artworks', slug=show.slug)
        selected_artworks = [s.artwork for s in selected_subs]
        if selected_artworks:
            show.artworks.add(*selected_artworks)
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

        # Publish from Draft: transition to Published and send acceptance/rejection emails
        if show.status == Show.STATUS_DRAFT:
            show.status = Show.STATUS_PUBLISHED
            show.save(update_fields=['status'])
            send_submission_emails(show)

        return redirect(show)

    context = {
        'show': show,
        'to_add': to_add,
        'to_keep': to_keep,
        'to_remove': to_remove,
        'selected_submissions': selected_subs,
        'rejected_submissions': rejected_subs,
        'to_reject': to_reject,
        'will_publish': show.status == Show.STATUS_DRAFT,
        'undecided_count': undecided_count,
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
    show = submission.show
    if request.method == 'POST':
        submission.delete()
    return redirect(show)
