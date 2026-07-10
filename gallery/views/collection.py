import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Exists, OuterRef, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView

from gallery.models import Artist, Artwork
from gallery.models.collection import CollectionPiece, SavedArtwork
from gallery.permissions import can_manage_artwork, is_curator_user, is_staff_user, visible_artwork_queryset


def artwork_autocomplete(request):
    """JSON search: artworks by name, for Select2."""
    q = request.GET.get('q', '').strip()
    qs = Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct()
    if q:
        qs = qs.filter(name__icontains=q)
    results = [
        {'id': a.pk, 'text': f'{a.name} — {", ".join(str(ar) for ar in a.artists.all())}'}
        for a in qs.prefetch_related('artists').order_by('name')[:20]
    ]
    return JsonResponse({'results': results})


def artist_autocomplete(request):
    """JSON search: artists by name."""
    q = request.GET.get('q', '').strip()
    qs = Artist.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    results = [{'id': a.pk, 'text': str(a)} for a in qs.order_by('name')[:20]]
    return JsonResponse({'results': results})


@login_required
def toggle_save(request, pk):
    """AJAX POST: toggle SavedArtwork for the current user."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    artwork = get_object_or_404(
        Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct(), pk=pk
    )
    obj, created = SavedArtwork.objects.get_or_create(user=request.user, artwork=artwork)
    if not created:
        obj.delete()
        return JsonResponse({'saved': False})
    return JsonResponse({'saved': True})


@login_required
def artwork_add_to_collection(request, pk):
    """Any logged-in user can claim ownership of an artwork (status=pending)."""
    if request.method != 'POST':
        return redirect('gallery:artwork_detail', pk=pk)
    artwork = get_object_or_404(
        Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct(), pk=pk
    )
    piece, created = CollectionPiece.objects.get_or_create(
        collector=request.user,
        artwork=artwork,
        defaults={
            'purchase_date': request.POST.get('purchase_date') or None,
            'purchase_price': request.POST.get('purchase_price') or None,
            'notes': request.POST.get('notes', ''),
            'status': CollectionPiece.STATUS_PENDING,
        },
    )
    if not created:
        if request.POST.get('purchase_date'):
            piece.purchase_date = request.POST.get('purchase_date')
        if request.POST.get('purchase_price'):
            piece.purchase_price = request.POST.get('purchase_price')
        if request.POST.get('notes'):
            piece.notes = request.POST.get('notes', '')
        piece.save()
    return redirect(artwork.get_absolute_url())


@login_required
def remove_collection_piece(request, pk):
    """Collector removes their own collection claim; staff can remove any."""
    if request.method != 'POST':
        raise PermissionDenied
    piece = get_object_or_404(CollectionPiece, pk=pk)
    if piece.collector != request.user and not is_staff_user(request.user):
        raise PermissionDenied
    artwork = piece.artwork
    piece.delete()
    return redirect(artwork.get_absolute_url())


@login_required
def confirm_collection_piece(request, pk):
    """Artist confirms or declines a collector's ownership claim."""
    if request.method != 'POST':
        raise PermissionDenied
    piece = get_object_or_404(CollectionPiece, pk=pk)
    artist = Artist.objects.filter(user=request.user, artworks=piece.artwork).first()
    if not artist and not is_curator_user(request.user):
        raise PermissionDenied
    action = request.POST.get('action')
    if action == 'confirm':
        piece.status = CollectionPiece.STATUS_CONFIRMED
        piece.confirmed_by = artist
        piece.confirmed_at = timezone.now()
        piece.save()
    elif action == 'decline':
        piece.status = CollectionPiece.STATUS_DECLINED
        piece.confirmed_by = artist
        piece.confirmed_at = timezone.now()
        piece.save()
    return redirect(piece.artwork.get_absolute_url())


def user_autocomplete(request):
    """JSON search: users by name or email. Staff only."""
    if not is_staff_user(request.user):
        return JsonResponse({'results': []}, status=403)
    User = get_user_model()
    q = request.GET.get('q', '').strip()
    qs = User.objects.filter(is_active=True)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) |
            Q(email__icontains=q) | Q(username__icontains=q)
        )
    results = [
        {'id': u.pk, 'text': u.get_full_name() or u.username, 'email': u.email}
        for u in qs.order_by('last_name', 'first_name')[:20]
    ]
    return JsonResponse({'results': results})


@login_required
def staff_record_ownership(request, pk):
    """Staff-only: create or update a CollectionPiece for any user."""
    if not is_staff_user(request.user):
        raise PermissionDenied
    artwork = get_object_or_404(Artwork, pk=pk)
    if request.method == 'GET':
        from django.shortcuts import render as _render
        existing = CollectionPiece.objects.filter(artwork=artwork).select_related('collector').order_by('status', '-created_at')
        return _render(request, 'gallery/staff_record_ownership_form.html', {
            'artwork': artwork,
            'existing': existing,
        })
    User = get_user_model()
    collector_id = request.POST.get('collector_id')
    if not collector_id:
        messages.error(request, 'Please select a collector.')
        return redirect(artwork.get_absolute_url())
    collector = get_object_or_404(User, pk=collector_id)
    status = request.POST.get('status', CollectionPiece.STATUS_CONFIRMED)
    if status not in dict(CollectionPiece.STATUS_CHOICES):
        status = CollectionPiece.STATUS_CONFIRMED
    confirmed_by = request.user.artists.order_by('-created_at').first() if status == CollectionPiece.STATUS_CONFIRMED else None
    confirmed_at = timezone.now() if status == CollectionPiece.STATUS_CONFIRMED else None
    piece, created = CollectionPiece.objects.get_or_create(
        collector=collector,
        artwork=artwork,
        defaults={
            'status': status,
            'purchase_date': request.POST.get('purchase_date') or None,
            'purchase_price': request.POST.get('purchase_price') or None,
            'commission_amount': request.POST.get('commission_amount') or 0,
            'notes': request.POST.get('notes', ''),
            'confirmed_by': confirmed_by,
            'confirmed_at': confirmed_at,
        },
    )
    if not created:
        piece.status = status
        piece.confirmed_by = confirmed_by
        piece.confirmed_at = confirmed_at
        if request.POST.get('purchase_date'):
            piece.purchase_date = request.POST.get('purchase_date')
        if request.POST.get('purchase_price'):
            piece.purchase_price = request.POST.get('purchase_price')
        if request.POST.get('commission_amount') is not None:
            piece.commission_amount = request.POST.get('commission_amount') or 0
        if request.POST.get('notes'):
            piece.notes = request.POST.get('notes', '')
        piece.save()
    name = collector.get_full_name() or collector.username
    messages.success(request, f'Ownership {"recorded" if created else "updated"} for {name}.')
    return redirect(artwork.get_absolute_url())


@login_required
def reorder_saved_artworks(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    ids = json.loads(request.body).get('ids', [])
    for order, pk in enumerate(ids, start=1):
        SavedArtwork.objects.filter(pk=pk, user=request.user).update(display_order=order)
    return JsonResponse({'ok': True})


@login_required
def reorder_collection_pieces(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    data = json.loads(request.body)
    ids = data.get('ids', [])
    collector_id = data.get('collector_id')
    if collector_id and is_staff_user(request.user):
        collector = get_object_or_404(get_user_model(), pk=collector_id)
    else:
        collector = request.user
    for order, pk in enumerate(ids, start=1):
        CollectionPiece.objects.filter(pk=pk, collector=collector).update(display_order=order)
    return JsonResponse({'ok': True})


class CollectorsListView(ListView):
    template_name = 'gallery/collectors.html'
    context_object_name = 'collector_rows'

    def get_queryset(self):
        # Required by ListView; actual data built in get_context_data.
        return []

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()

        confirmed_filter = Q(collection_pieces__status=CollectionPiece.STATUS_CONFIRMED)
        owners = list(
            User.objects
            .filter(confirmed_filter)
            .annotate(confirmed_count=Count('collection_pieces', filter=confirmed_filter))
            .order_by('-confirmed_count')
            .distinct()
        )

        pinned_filter = Q(saved_artworks__isnull=False)
        pinners_qs = User.objects.filter(pinned_filter)
        if not is_staff_user(self.request.user):
            from gallery.models import Artist
            pinners_qs = pinners_qs.filter(
                Exists(Artist.objects.filter(
                    user=OuterRef('pk'),
                    artworks__shows__isnull=False,
                ))
            )
        pinners = list(
            pinners_qs
            .annotate(pinned_count=Count('saved_artworks', distinct=True))
            .order_by('-pinned_count')
            .distinct()
        )

        context['owners'] = owners
        context['pinners'] = pinners
        return context
