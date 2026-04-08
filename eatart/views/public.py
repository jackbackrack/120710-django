from datetime import datetime

from django.shortcuts import render

from piece.models import Artist, Event, Piece, Show


def index(request):
    pieces = Piece.objects.all()[:6]
    shows = Show.objects.order_by('-start')
    artists = Artist.objects.order_by('name')
    now = datetime.now()
    current_show = Show.objects.filter(start__lte=now, end__gte=now).first()
    is_current_show = False
    is_next_show = False
    next_show = None
    next_event = Event.objects.filter(date__gte=now).order_by('date').first()

    if current_show:
        next_show = current_show
        is_current_show = True
    else:
        next_show = Show.objects.filter(start__gt=now).order_by('start').first()
        if next_show:
            is_next_show = True

    return render(request, 'public/index.html', {
        'is_current_show': is_current_show,
        'is_next_show': is_next_show,
        'next_show': next_show,
        'next_event': next_event,
        'pieces': pieces,
        'shows': shows,
        'artists': artists,
    })


def contact(request):
    return render(request, 'public/contact.html')


def visit(request):
    return render(request, 'public/visit.html')


def about(request):
    return render(request, 'public/about.html')


def howto(request):
    return render(request, 'public/howto.html')
