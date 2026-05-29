from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect

from gallery.models import Artist, Artwork
from gallery.permissions import can_manage_artist, can_manage_artwork


@login_required
def regenerate_artwork_thumbnail(request, pk):
    artwork = get_object_or_404(Artwork, pk=pk)
    if not can_manage_artwork(request.user, artwork):
        raise Http404
    if request.method == 'POST' and artwork.image:
        artwork.card_thumbnail.generate(force=True)
    return redirect(request.META.get('HTTP_REFERER', artwork.get_absolute_url()))


@login_required
def regenerate_artist_thumbnail(request, pk):
    artist = get_object_or_404(Artist, pk=pk)
    if not can_manage_artist(request.user, artist):
        raise Http404
    if request.method == 'POST' and artist.image:
        artist.card_thumbnail.generate(force=True)
    return redirect(request.META.get('HTTP_REFERER', artist.get_absolute_url()))
