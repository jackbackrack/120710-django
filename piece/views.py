from django.shortcuts import render, get_object_or_404

from .models import Show, Artist, Piece

def detail(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    return render(request, 'piece/piece_detail.html', {
      'piece': piece
    })

def show_detail(request, pk):
    show = get_object_or_404(Show, pk=pk)
    pieces = Piece.objects.filter(shows__id = pk).order_by('artists__name', 'name')
    return render(request, 'piece/show_detail.html', {
        'show': show,
        'pieces': pieces
    })

def piece_detail(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    return render(request, 'piece/piece_detail.html', {
      'piece': piece
    })

def artist_detail(request, pk):
    artist = get_object_or_404(Artist, pk=pk)
    return render(request, 'piece/artist_detail.html', {
      'artist': artist
    })
