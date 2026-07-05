from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView

from gallery.models import Artist, Artwork
from gallery.permissions import (
    is_curator_user,
    is_staff_user,
)


def _can_manage_collection(user):
    return user.is_authenticated and (is_staff_user(user) or is_curator_user(user))


def artwork_autocomplete(request):
    """JSON search: artworks by other artists, for Select2 in artist edit form."""
    artist_pk = request.GET.get('artist_pk')
    q = request.GET.get('q', '').strip()
    qs = Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct()
    if artist_pk:
        qs = qs.exclude(artists__pk=artist_pk)
    if q:
        qs = qs.filter(name__icontains=q)
    results = [
        {'id': a.pk, 'text': f'{a.name} — {", ".join(str(ar) for ar in a.artists.all())}'}
        for a in qs.prefetch_related('artists').order_by('name')[:20]
    ]
    return JsonResponse({'results': results})


def artist_autocomplete(request):
    """JSON search: artists, for Select2 on artwork detail 'add to collection'."""
    q = request.GET.get('q', '').strip()
    qs = Artist.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    results = [{'id': a.pk, 'text': str(a)} for a in qs.order_by('name')[:20]]
    return JsonResponse({'results': results})


@login_required
def artwork_add_to_collection(request, pk):
    if not _can_manage_collection(request.user):
        raise PermissionDenied
    artwork = get_object_or_404(Artwork, pk=pk)
    artist_pk = request.POST.get('artist_pk') or ''
    if not artist_pk.strip():
        from django.contrib import messages
        messages.error(request, 'Please select an artist before adding to a collection.')
        return redirect(artwork.get_absolute_url())
    artist = get_object_or_404(Artist, pk=artist_pk)
    artist.collection.add(artwork)
    return redirect(artwork.get_absolute_url())


@login_required
def artwork_remove_from_collection(request, pk):
    if not _can_manage_collection(request.user):
        raise PermissionDenied
    artwork = get_object_or_404(Artwork, pk=pk)
    artist_pk = request.POST.get('artist_pk')
    artist = get_object_or_404(Artist, pk=artist_pk)
    artist.collection.remove(artwork)
    return redirect(artwork.get_absolute_url())


class CollectorsListView(ListView):
    model = Artist
    template_name = 'gallery/collectors.html'
    context_object_name = 'artists'

    def get_queryset(self):
        return (
            Artist.objects
            .filter(collection__isnull=False)
            .annotate(collection_count=Count('collection'))
            .order_by('-collection_count')
            .distinct()
        )
