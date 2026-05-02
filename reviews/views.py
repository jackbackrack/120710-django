from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from gallery.models import Artwork, Show
from gallery.permissions import can_manage_show, can_view_reviews, is_curator_user, is_juror_for_show
from reviews.forms import ArtworkReviewForm, ShowJurorAssignmentForm
from reviews.models import ArtworkReview, ShowJuror


@login_required
def show_review_dashboard(request, show_slug):
    """
    Curator/juror dashboard for a show's reviews.

    Curators see all reviews aggregated per artwork.
    Jurors see only their own reviews plus artwork list for their assigned show.
    """
    show = get_object_or_404(Show, slug=show_slug)
    if not can_view_reviews(request.user, show):
        raise Http404

    # Artworks in this show
    artworks = Artwork.objects.filter(shows=show).order_by('name')

    if is_curator_user(request.user):
        # Annotate artworks with average rating and review count for this show
        artworks = artworks.annotate(
            avg_rating=Avg('reviews__rating', filter=Q(reviews__show=show)),
            review_count=Count('reviews', filter=Q(reviews__show=show)),
        )
        all_reviews = ArtworkReview.objects.filter(show=show).select_related(
            'artwork', 'juror'
        ).order_by('artwork__name', 'juror__last_name')
        jurors = show.jurors.select_related('user').order_by('user__last_name')
        context = {
            'show': show,
            'artworks': artworks,
            'all_reviews': all_reviews,
            'jurors': jurors,
            'is_curator': True,
        }
    else:
        # Juror: show their own reviews and remaining artworks to review
        my_reviews = ArtworkReview.objects.filter(
            show=show, juror=request.user
        ).select_related('artwork')
        reviewed_ids = set(my_reviews.values_list('artwork_id', flat=True))
        pending_artworks = artworks.exclude(pk__in=reviewed_ids)
        context = {
            'show': show,
            'my_reviews': my_reviews,
            'pending_artworks': pending_artworks,
            'is_curator': False,
        }

    return render(request, 'reviews/show_review_dashboard.html', context)


@login_required
def artwork_review(request, show_slug, artwork_slug):
    """
    Juror submits or edits their review of a specific artwork in a show.
    Curators can view all reviews for this artwork in this show.
    """
    show = get_object_or_404(Show, slug=show_slug)
    artwork = get_object_or_404(Artwork, slug=artwork_slug, shows=show)

    if not is_juror_for_show(request.user, show) and not can_manage_show(request.user, show):
        raise Http404

    if can_manage_show(request.user, show):
        reviews = ArtworkReview.objects.filter(show=show, artwork=artwork).select_related('juror')
        context = {
            'show': show,
            'artwork': artwork,
            'reviews': reviews,
            'is_curator': True,
        }
        return render(request, 'reviews/artwork_review_detail.html', context)

    # Juror path — create or edit their own review
    instance = ArtworkReview.objects.filter(
        show=show, artwork=artwork, juror=request.user
    ).first()

    if request.method == 'POST':
        form = ArtworkReviewForm(request.POST, instance=instance)
        if form.is_valid():
            review = form.save(commit=False)
            review.show = show
            review.artwork = artwork
            review.juror = request.user
            review.save()
            return redirect('reviews:show_review_dashboard', show_slug=show.slug)
    else:
        form = ArtworkReviewForm(instance=instance)

    context = {
        'show': show,
        'artwork': artwork,
        'form': form,
        'existing_review': instance,
        'is_curator': False,
    }
    return render(request, 'reviews/artwork_review_form.html', context)


@login_required
def curator_edit_review(request, show_slug, artwork_slug, review_id):
    """Curator/staff edits a juror review for an artwork in a show."""
    show = get_object_or_404(Show, slug=show_slug)
    if not can_manage_show(request.user, show):
        raise Http404

    artwork = get_object_or_404(Artwork, slug=artwork_slug, shows=show)
    review = get_object_or_404(ArtworkReview, pk=review_id, show=show, artwork=artwork)

    if request.method == 'POST':
        form = ArtworkReviewForm(request.POST, instance=review)
        if form.is_valid():
            form.save()
            return redirect('reviews:artwork_review', show_slug=show.slug, artwork_slug=artwork.slug)
    else:
        form = ArtworkReviewForm(instance=review)

    context = {
        'show': show,
        'artwork': artwork,
        'form': form,
        'review': review,
        'is_curator': True,
    }
    return render(request, 'reviews/curator_review_edit_form.html', context)


@login_required
def show_juror_assignment(request, show_slug):
    """Curator/staff manages juror assignments for a show."""
    show = get_object_or_404(Show, slug=show_slug)
    if not can_manage_show(request.user, show):
        raise Http404

    assigned_jurors = ShowJuror.objects.filter(show=show).select_related('user').order_by(
        'user__last_name', 'user__first_name', 'user__username'
    )

    if request.method == 'POST' and request.POST.get('action') == 'remove':
        assignment = get_object_or_404(ShowJuror, pk=request.POST.get('assignment_id'), show=show)
        assignment.delete()
        return redirect('reviews:show_juror_assignment', show_slug=show.slug)

    form = ShowJurorAssignmentForm()
    assigned_ids = assigned_jurors.values_list('user_id', flat=True)
    form.fields['user'].queryset = form.fields['user'].queryset.exclude(pk__in=assigned_ids)

    if request.method == 'POST' and request.POST.get('action') == 'assign':
        form = ShowJurorAssignmentForm(request.POST)
        form.fields['user'].queryset = form.fields['user'].queryset.exclude(pk__in=assigned_ids)
        if form.is_valid():
            ShowJuror.objects.create(
                show=show,
                user=form.cleaned_data['user'],
                assigned_by=request.user,
            )
            return redirect('reviews:show_juror_assignment', show_slug=show.slug)

    context = {
        'show': show,
        'form': form,
        'assigned_jurors': assigned_jurors,
    }
    return render(request, 'reviews/show_juror_assignment.html', context)

