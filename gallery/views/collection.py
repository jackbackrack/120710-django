from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView

from gallery.models import Artist, Artwork
from gallery.models.collection import CollectionPiece, SavedArtwork
from gallery.permissions import can_manage_artwork, is_staff_user, visible_artwork_queryset


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
    if not artist and not is_staff_user(request.user):
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


class CollectorsListView(ListView):
    template_name = 'gallery/collectors.html'
    context_object_name = 'collector_rows'

    def get_queryset(self):
        User = get_user_model()
        confirmed_filter = Q(collection_pieces__status=CollectionPiece.STATUS_CONFIRMED)
        return (
            User.objects
            .filter(confirmed_filter)
            .annotate(confirmed_count=Count('collection_pieces', filter=confirmed_filter))
            .order_by('-confirmed_count')
            .distinct()
        )
