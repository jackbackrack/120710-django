import datetime as dt

from django.shortcuts import render

from accounts.roles import ARTIST_GROUP, CURATOR_GROUP, JUROR_GROUP, STAFF_GROUP
from eatart.role_docs import GENERAL_GUIDE, ROLE_DOCUMENTATION
from eatart.schemaorg.mappers import dump_json_ld, gallery_to_schema, schema_to_dict
from gallery.models import Event, Show


def index(request):
    today = dt.date.today()
    base = Show.objects.prefetch_related('curators', 'tags', 'events')
    current_shows = list(base.filter(start__lte=today, end__gte=today).order_by('-start'))
    future_shows = list(base.filter(start__gt=today).order_by('start'))
    past_shows = list(base.filter(end__lt=today).order_by('-start'))

    hero_show = current_shows[0] if current_shows else (future_shows[0] if future_shows else None)
    hero_is_current = bool(current_shows) and hero_show == current_shows[0]

    next_event = Event.objects.filter(date__gte=today).order_by('date').first()

    return render(request, 'public/index.html', {
        'hero_show': hero_show,
        'hero_is_current': hero_is_current,
        'next_event': next_event,
        'current_shows': current_shows,
        'future_shows': future_shows,
        'past_shows': past_shows,
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
