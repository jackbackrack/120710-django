from django.shortcuts import render, get_object_or_404

from .models import Show, Artist, Piece, Event

def detail(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    return render(request, 'piece/piece_detail.html', {
      'piece': piece
    })

def event_detail(request, pk):
    event = get_object_or_404(Event, pk=pk)
    return render(request, 'piece/event_detail.html', {
      'event': event
    })

def show_detail(request, pk):
    show = get_object_or_404(Show, pk=pk)
    # pieces = Piece.objects.filter(shows__id = pk).order_by('artists__name', 'name')
    pieces = Piece.objects.filter(shows__id = pk).order_by('name')
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
