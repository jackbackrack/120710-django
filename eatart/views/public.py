from datetime import datetime

from django.shortcuts import render

from accounts.roles import ARTIST_GROUP, CURATOR_GROUP, JUROR_GROUP, STAFF_GROUP
from eatart.role_docs import GENERAL_GUIDE, ROLE_DOCUMENTATION
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
    active_roles = []
    role_priority = [STAFF_GROUP, CURATOR_GROUP, JUROR_GROUP, ARTIST_GROUP]

    if request.user.is_authenticated:
        group_names = set(request.user.groups.values_list('name', flat=True))
        if request.user.is_staff or request.user.is_superuser:
            active_roles.append(STAFF_GROUP)
        for role_name in role_priority:
            if role_name == STAFF_GROUP:
                continue
            if role_name in group_names:
                active_roles.append(role_name)

    role_guides = [ROLE_DOCUMENTATION[role_name] for role_name in active_roles if role_name in ROLE_DOCUMENTATION]

    context = {
        'general_guide': GENERAL_GUIDE,
        'role_guides': role_guides,
        'active_roles': active_roles,
    }
    return render(request, 'public/howto.html', context)
