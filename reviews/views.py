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


def _attach_computed_weighted(reviews, criteria):
    """Attach .computed_weighted (partial weighted sum 0-100) to each ArtworkReview object."""
    if not criteria:
        return
    criteria_by_id = {c.pk: c for c in criteria}
    for review in reviews:
        total = sum(
            cs.score * criteria_by_id[cs.criterion_id].percentage / 100.0
            for cs in review.criterion_scores.all()
            if cs.criterion_id in criteria_by_id
        )
        review.computed_weighted = round(total, 1) if total > 0 else None


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
            'jurors': jurors,
            'juror_progress': juror_progress,
            'all_jurors_done': all_jurors_done,
            'total_submissions': total_submissions,
            'criteria': criteria,
            'is_curator': True,
            'is_also_juror': False,
        }
        if is_juror_for_show(request.user, show):
            my_reviews = list(
                ArtworkReview.objects
                .filter(show=show, juror=request.user)
                .select_related('artwork')
                .prefetch_related('criterion_scores__criterion')
            )
            _attach_computed_weighted(my_reviews, criteria)
            reviewed_ids = {r.artwork_id for r in my_reviews}
            pending = Artwork.objects.filter(submissions__show=show).exclude(pk__in=reviewed_ids).order_by('name')
            context['is_also_juror'] = True
            context['my_reviews'] = my_reviews
            context['pending_artworks'] = pending
    else:
        my_reviews = list(
            ArtworkReview.objects
            .filter(show=show, juror=request.user)
            .select_related('artwork')
            .prefetch_related('criterion_scores__criterion')
        )
        _attach_computed_weighted(my_reviews, criteria)
        reviewed_ids = {r.artwork_id for r in my_reviews}
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
            'year': str(artwork.end_year) if artwork.end_year else '',
            'medium': artwork.medium or '',
            'dimensions': artwork.placard_dimensions,
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


# ── Curation slideshow ─────────────────────────────────────────────────────

@login_required
def curation_data(request, show_slug):
    """JSON endpoint: all submissions with artwork info and juror scores for curator sorting."""
    show = get_object_or_404(Show, slug=show_slug)
    if not can_manage_show(request.user, show):
        raise Http404

    from gallery.models.submissions import ArtworkSubmission
    criteria = list(show.rubric_criteria.all())

    subs_qs = (
        ArtworkSubmission.objects
        .filter(show=show)
        .select_related('artwork')
        .prefetch_related('artwork__artists')
        .order_by('submitted_at')
    )
    decision_filter = request.GET.get('decision')
    if decision_filter in (ArtworkSubmission.UNDECIDED,
                           ArtworkSubmission.CURATOR_SELECTED,
                           ArtworkSubmission.CURATOR_REJECTED):
        subs_qs = subs_qs.filter(curator_decision=decision_filter)
    submissions = list(subs_qs)

    # Optional ?juror=<user_id> restricts to one juror's scores, so the slideshow
    # shows that juror's own ranking/order instead of the cross-juror average.
    juror_user = None
    juror_id = request.GET.get('juror')
    if juror_id:
        if not show.jurors.filter(user_id=juror_id).exists():
            raise Http404
        from django.contrib.auth import get_user_model
        juror_user = get_user_model().objects.filter(pk=juror_id).first()

    reviews_qs = ArtworkReview.objects.filter(show=show)
    if juror_user is not None:
        reviews_qs = reviews_qs.filter(juror=juror_user)
    all_reviews = list(
        reviews_qs
        .select_related('juror')
        .prefetch_related('criterion_scores', 'juror__artists')
        .order_by('juror__last_name', 'juror__first_name')
    )

    reviews_by_artwork = {}
    for review in all_reviews:
        reviews_by_artwork.setdefault(review.artwork_id, []).append(review)

    def juror_display(user):
        if user is None:
            return '(deleted)'
        artist = user.artists.first()
        return artist.full_name if artist else (user.get_full_name() or user.username)

    def fmt_dim(val):
        if val is None:
            return ''
        f = float(val)
        return str(int(f)) if f == int(f) else str(round(f, 2))

    def artwork_dimensions(aw):
        parts = [fmt_dim(v) for v in (aw.width_inches, aw.height_inches, aw.depth_inches) if v]
        return (' × '.join(parts) + ' in') if len(parts) >= 2 else ''

    def weighted_for_scores(scores_dict):
        if not criteria:
            return None
        total = sum(scores_dict[c.pk] * c.percentage / 100.0 for c in criteria if c.pk in scores_dict)
        scored = sum(c.percentage for c in criteria if c.pk in scores_dict)
        return round(total, 1) if scored > 0 else None

    artwork_list = []
    for sub in submissions:
        aw = sub.artwork
        reviews = reviews_by_artwork.get(aw.pk, [])

        juror_scores = []
        all_weighted = []
        for review in reviews:
            scores_dict = {cs.criterion_id: cs.score for cs in review.criterion_scores.all()}
            w = weighted_for_scores(scores_dict) if criteria else review.rating
            if w is not None:
                all_weighted.append(w)
            juror_scores.append({
                'name': juror_display(review.juror),
                'criteria': scores_dict,
                'rating': review.rating,
                'weighted': w,
            })

        overall = round(sum(all_weighted) / len(all_weighted), 1) if all_weighted else None

        artwork_list.append({
            'submission_id': sub.pk,
            'slug': aw.slug,
            'name': aw.name,
            'artists': [] if show.blind_review else [a.full_name for a in aw.artists.all()],
            'year': str(aw.end_year) if aw.end_year else '',
            'medium': aw.medium or '',
            'dimensions': artwork_dimensions(aw),
            'img': aw.slideshow.url if aw.image else '',
            'thumb': aw.card_sm.url if aw.image else '',
            'detail_url': aw.get_absolute_url() + ('?blind=1' if show.blind_review else ''),
            'decision': sub.curator_decision,
            'weighted_score': overall,
            'juror_scores': juror_scores,
        })

    artwork_list.sort(
        key=lambda x: x['weighted_score'] if x['weighted_score'] is not None else -1,
        reverse=True,
    )

    return JsonResponse({
        'show_slug': show.slug,
        'blind_review': show.blind_review,
        'juror_name': juror_display(juror_user) if juror_user else None,
        'criteria': [{'id': c.pk, 'name': c.name, 'percentage': c.percentage} for c in criteria],
        'artworks': artwork_list,
    })


@login_required
def save_decision(request, show_slug):
    """JSON endpoint: save curator_decision on a submission."""
    if request.method != 'POST':
        raise Http404
    show = get_object_or_404(Show, slug=show_slug)
    if not can_manage_show(request.user, show):
        raise Http404

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, Exception):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    from gallery.models.submissions import ArtworkSubmission
    valid = {ArtworkSubmission.UNDECIDED, ArtworkSubmission.CURATOR_SELECTED, ArtworkSubmission.CURATOR_REJECTED}
    decision = data.get('decision', ArtworkSubmission.UNDECIDED)
    if decision not in valid:
        return JsonResponse({'ok': False, 'error': 'Invalid decision'}, status=400)

    sub = get_object_or_404(ArtworkSubmission, pk=data.get('submission_id'), show=show)
    sub.curator_decision = decision
    sub.save(update_fields=['curator_decision'])
    return JsonResponse({'ok': True})
