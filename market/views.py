from django.shortcuts import render
from datetime import datetime

from piece.models import Piece, Artist, Show

def index(request):
    pieces = Piece.objects.filter()[0:6]
    shows = Show.objects.filter().order_by('-start')
    artists = Artist.objects.filter().order_by('name')
    now = datetime.now()
    current_show = Show.objects.filter(start__lte=now, end__gte=now).first()
    is_current_show = False
    is_next_show = False
    next_show = None

    if current_show:
        next_show = current_show
        is_current_show = True
    else:
        next_show = Show.objects.filter(start__gt=now).order_by('start').first()
        if next_show:
            is_next_show = True

    return render(request, 'market/index.html', {
        'is_current_show': is_current_show,
        'is_next_show': is_next_show,
        'next_show': next_show,
        'pieces': pieces,
        'shows': shows,
        'artists': artists,
    })

def contact(request):
    return render(request, 'market/contact.html')

def about(request):
    return render(request, 'market/about.html')

def howto(request):
    return render(request, 'market/howto.html')

def signup(request):
    return render(request, 'market/signup.html')

