import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from gallery.models import Artwork, Show
from gallery.permissions import can_manage_show, can_view_reviews, is_curator_user, is_juror_for_show
from reviews.forms import ArtworkReviewForm, RubricCriterionFormSet, ShowJurorAssignmentForm
from reviews.models import ArtworkReview, CriterionScore, RubricCriterion, ShowJuror


def _compute_weighted_scores(show, criteria):
    """Return {artwork_id: weighted_score} using percentage/100 as each criterion's factor."""
    if not criteria:
        return {}
    rows = (
        CriterionScore.objects
        .filter(review__show=show)
        .values('review__artwork_id', 'criterion_id', 'criterion__percentage')
        .annotate(avg_score=Avg('score'))
    )
    sums = {}
    for row in rows:
        aid = row['review__artwork_id']
        sums[aid] = sums.get(aid, 0.0) + row['avg_score'] * row['criterion__percentage'] / 100.0
    return sums


@login_required
def show_review_dashboard(request, show_slug):
    show = get_object_or_404(Show, slug=show_slug)
    if not can_view_reviews(request.user, show):
        raise Http404

    query = request.GET.get('q', '').strip()
    artworks = Artwork.objects.filter(submissions__show=show).order_by('name')
    if query:
        artworks = artworks.filter(
            Q(name__icontains=query) |
            Q(artists__first_name__icontains=query) |
            Q(artists__last_name__icontains=query)
        ).distinct()
    criteria = list(show.rubric_criteria.all())

    if is_curator_user(request.user):
        artworks = list(artworks.annotate(
            avg_rating=Avg('reviews__rating', filter=Q(reviews__show=show)),
            review_count=Count('reviews', filter=Q(reviews__show=show), distinct=True),
        ))
        if criteria:
            weighted_scores = _compute_weighted_scores(show, criteria)
            for artwork in artworks:
                artwork.weighted_score = weighted_scores.get(artwork.pk)
        all_reviews = (
            ArtworkReview.objects
            .filter(show=show)
            .select_related('artwork', 'juror')
            .prefetch_related('criterion_scores__criterion')
            .order_by('artwork__name', 'juror__last_name')
        )
        jurors = list(show.jurors.select_related('user').order_by('user__last_name'))
        total_submissions = Artwork.objects.filter(submissions__show=show).count()
        review_counts = {
            row['juror']: row['n']
            for row in ArtworkReview.objects.filter(show=show).values('juror').annotate(n=Count('id'))
        }
        juror_progress = [
            {
                'assignment': j,
                'name': j.user.artists.first().full_name if j.user.artists.exists() else (j.user.get_full_name() or j.user.username),
                'done': review_counts.get(j.user_id, 0),
                'total': total_submissions,
                'finished': review_counts.get(j.user_id, 0) >= total_submissions and total_submissions > 0,
            }
            for j in jurors
        ]
        all_jurors_done = bool(jurors) and total_submissions > 0 and all(p['finished'] for p in juror_progress)
        context = {
            'show': show,
            'artworks': artworks,
            'all_reviews': all_reviews,
            'jurors': jurors,
            'juror_progress': juror_progress,
            'all_jurors_done': all_jurors_done,
            'total_submissions': total_submissions,
            'criteria': criteria,
            'is_curator': True,
            'is_also_juror': False,
        }
        if is_juror_for_show(request.user, show):
            my_reviews = (
                ArtworkReview.objects
                .filter(show=show, juror=request.user)
                .select_related('artwork')
                .prefetch_related('criterion_scores__criterion')
            )
            reviewed_ids = set(my_reviews.values_list('artwork_id', flat=True))
            pending = Artwork.objects.filter(submissions__show=show).exclude(pk__in=reviewed_ids).order_by('name')
            context['is_also_juror'] = True
            context['my_reviews'] = my_reviews
            context['pending_artworks'] = pending
    else:
        my_reviews = (
            ArtworkReview.objects
            .filter(show=show, juror=request.user)
            .select_related('artwork')
            .prefetch_related('criterion_scores__criterion')
        )
        reviewed_ids = set(my_reviews.values_list('artwork_id', flat=True))
        pending_artworks = artworks.exclude(pk__in=reviewed_ids)
        context = {
            'show': show,
            'my_reviews': my_reviews,
            'pending_artworks': pending_artworks,
            'criteria': criteria,
            'is_curator': False,
        }

    return render(request, 'reviews/show_review_dashboard.html', context)


@login_required
def artwork_review(request, show_slug, artwork_slug):
    show = get_object_or_404(Show, slug=show_slug)
    artwork = get_object_or_404(Artwork, slug=artwork_slug, submissions__show=show)

    if not is_juror_for_show(request.user, show) and not can_manage_show(request.user, show):
        raise Http404

    view_all = request.GET.get('all') == '1'
    if can_manage_show(request.user, show) and (not is_juror_for_show(request.user, show) or view_all):
        reviews = (
            ArtworkReview.objects
            .filter(show=show, artwork=artwork)
            .select_related('juror')
            .prefetch_related('criterion_scores__criterion')
        )
        criteria = list(show.rubric_criteria.all())
        context = {
            'show': show,
            'artwork': artwork,
            'reviews': reviews,
            'criteria': criteria,
            'is_curator': True,
        }
        return render(request, 'reviews/artwork_review_detail.html', context)

    instance = ArtworkReview.objects.filter(show=show, artwork=artwork, juror=request.user).first()

    if request.method == 'POST':
        form = ArtworkReviewForm(request.POST, show=show, instance=instance)
        if form.is_valid():
            review = form.save(commit=False)
            review.show = show
            review.artwork = artwork
            review.juror = request.user
            review.save()
            form.save_criterion_scores(review)
            return redirect('reviews:show_review_dashboard', show_slug=show.slug)
    else:
        form = ArtworkReviewForm(show=show, instance=instance)

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
    show = get_object_or_404(Show, slug=show_slug)
    if not can_manage_show(request.user, show):
        raise Http404

    artwork = get_object_or_404(Artwork, slug=artwork_slug, submissions__show=show)
    review = get_object_or_404(ArtworkReview, pk=review_id, show=show, artwork=artwork)

    if request.method == 'POST':
        form = ArtworkReviewForm(request.POST, show=show, instance=review)
        if form.is_valid():
            form.save()
            form.save_criterion_scores(review)
            return redirect('reviews:artwork_review', show_slug=show.slug, artwork_slug=artwork.slug)
    else:
        form = ArtworkReviewForm(show=show, instance=review)

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

    assigned_user_ids = assigned_jurors.values_list('user_id', flat=True)
    form = ShowJurorAssignmentForm()
    form.fields['artist'].queryset = form.fields['artist'].queryset.exclude(user_id__in=assigned_user_ids)

    if request.method == 'POST' and request.POST.get('action') == 'assign':
        form = ShowJurorAssignmentForm(request.POST)
        form.fields['artist'].queryset = form.fields['artist'].queryset.exclude(user_id__in=assigned_user_ids)
        if form.is_valid():
            ShowJuror.objects.create(
                show=show,
                user=form.cleaned_data['artist'].user,
                assigned_by=request.user,
            )
            return redirect('reviews:show_juror_assignment', show_slug=show.slug)

    context = {
        'show': show,
        'form': form,
        'assigned_jurors': assigned_jurors,
    }
    return render(request, 'reviews/show_juror_assignment.html', context)


@login_required
def copy_rubric_from_show(request, show_slug):
    show = get_object_or_404(Show, slug=show_slug)
    if not can_manage_show(request.user, show):
        raise Http404
    if request.method == 'POST':
        source_slug = request.POST.get('source_show')
        source = get_object_or_404(Show, slug=source_slug)
        source_criteria = list(source.rubric_criteria.order_by('order'))
        if source_criteria:
            RubricCriterion.objects.filter(show=show).delete()
            for c in source_criteria:
                RubricCriterion.objects.create(
                    show=show,
                    name=c.name,
                    description=c.description,
                    percentage=c.percentage,
                    order=c.order,
                )
    return redirect('reviews:manage_rubric_criteria', show_slug=show.slug)


@login_required
def manage_rubric_criteria(request, show_slug):
    show = get_object_or_404(Show, slug=show_slug)
    if not can_manage_show(request.user, show):
        raise Http404

    qs = RubricCriterion.objects.filter(show=show)

    if request.method == 'POST':
        formset = RubricCriterionFormSet(request.POST, queryset=qs)
        if formset.is_valid():
            instances = formset.save(commit=False)
            for instance in instances:
                instance.show = show
                instance.save()
            for obj in formset.deleted_objects:
                obj.delete()
            total_pct = sum(c.percentage for c in show.rubric_criteria.all())
            if show.rubric_criteria.exists() and abs(total_pct - 100) > 0.01:
                messages.warning(request, f'Percentages sum to {total_pct:g}%, not 100%.')
            return redirect('reviews:manage_rubric_criteria', show_slug=show.slug)
    else:
        formset = RubricCriterionFormSet(queryset=qs)

    other_shows = (
        Show.objects
        .filter(rubric_criteria__isnull=False)
        .exclude(pk=show.pk)
        .distinct()
        .order_by('name')
    )
    context = {
        'show': show,
        'formset': formset,
        'criteria': qs,
        'other_shows': other_shows,
    }
    return render(request, 'reviews/rubric_criteria.html', context)


@login_required
def review_data(request, show_slug):
    """JSON endpoint: all artworks for a show with this juror's current scores."""
    show = get_object_or_404(Show, slug=show_slug)
    if not is_juror_for_show(request.user, show) and not can_manage_show(request.user, show):
        raise Http404

    artworks = list(
        Artwork.objects.filter(submissions__show=show)
        .prefetch_related('artists')
        .order_by('name')
    )
    criteria = list(show.rubric_criteria.all())

    my_reviews = {
        r.artwork_id: r
        for r in ArtworkReview.objects.filter(show=show, juror=request.user)
        .prefetch_related('criterion_scores')
    }

    artwork_data = []
    for artwork in artworks:
        review = my_reviews.get(artwork.pk)
        scores = {}
        if review:
            for cs in review.criterion_scores.all():
                scores[cs.criterion_id] = cs.score
        artwork_data.append({
            'slug': artwork.slug,
            'name': artwork.name,
            'artists': [] if show.blind_review else [a.full_name for a in artwork.artists.all()],
            'img': artwork.slideshow.url if artwork.image else '',
            'thumb': artwork.card_sm.url if artwork.image else '',
            'detail_url': artwork.get_absolute_url() + ('?blind=1' if show.blind_review else ''),
            'scores': scores,
            'rating': review.rating if review else None,
            'body': review.body if review else '',
            'reviewed': bool(review),
        })

    return JsonResponse({
        'show_slug': show.slug,
        'blind_review': show.blind_review,
        'criteria': [
            {'id': c.pk, 'name': c.name, 'description': c.description, 'percentage': c.percentage}
            for c in criteria
        ],
        'artworks': artwork_data,
    })


@login_required
def save_score(request, show_slug):
    """JSON endpoint: save one criterion score, overall rating, or body for one artwork."""
    if request.method != 'POST':
        raise Http404
    show = get_object_or_404(Show, slug=show_slug)
    if not is_juror_for_show(request.user, show):
        raise Http404

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, Exception):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    artwork_slug = data.get('artwork_slug', '')
    artwork = get_object_or_404(Artwork, slug=artwork_slug, submissions__show=show)

    review, _ = ArtworkReview.objects.get_or_create(
        show=show, artwork=artwork, juror=request.user,
        defaults={'rating': None, 'body': ''},
    )

    if 'criterion_id' in data and 'score' in data:
        criterion = get_object_or_404(RubricCriterion, pk=data['criterion_id'], show=show)
        CriterionScore.objects.update_or_create(
            review=review,
            criterion=criterion,
            defaults={'score': int(data['score'])},
        )

    if 'rating' in data and data['rating'] is not None:
        review.rating = int(data['rating'])
        review.save(update_fields=['rating', 'updated_at'])

    if 'body' in data:
        review.body = data['body']
        review.save(update_fields=['body', 'updated_at'])

    return JsonResponse({'ok': True})
