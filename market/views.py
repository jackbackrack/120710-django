from django.shortcuts import render

from piece.models import Piece, Artist, Show

def index(request):
    pieces = Piece.objects.filter()[0:6]
    shows = Show.objects.filter().order_by('-start')
    artists = Artist.objects.filter().order_by('name')
    return render(request, 'market/index.html', {
        'pieces': pieces,
        'shows': shows,
        'artists': artists,
    })

def contact(request):
    return render(request, 'market/contact.html')

