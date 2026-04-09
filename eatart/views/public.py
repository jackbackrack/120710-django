from datetime import datetime

from django.shortcuts import render

from eatart.schemaorg.mappers import dump_json_ld, gallery_to_schema, schema_to_dict
from gallery.models import Artist, Artwork, Event, Show


def index(request):
    artworks = Artwork.objects.all()[:6]
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
        'artworks': artworks,
        'shows': shows,
        'artists': artists,
        'structured_data_json': dump_json_ld(schema_to_dict(gallery_to_schema(request))),
    })


def contact(request):
    return render(request, 'public/contact.html')


def visit(request):
    return render(request, 'public/visit.html')


def about(request):
    return render(request, 'public/about.html')


def howto(request):
    return render(request, 'public/howto.html')
