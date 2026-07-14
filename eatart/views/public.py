import datetime as dt

from django.shortcuts import render

from eatart.role_docs import GENERAL_GUIDE, HOW_TO_GUIDES, ROLE_DOCUMENTATION
from eatart.schemaorg.mappers import dump_json_ld, gallery_to_schema, schema_to_dict
from gallery.models import LinkTreeEntry, Show
from gallery.permissions import can_delete_show, can_manage_show, is_curator_user, is_juror_user, is_staff_user, visible_show_queryset


def index(request):
    today = dt.date.today()
    base = Show.objects.prefetch_related('curators', 'tags', 'events')
    base = visible_show_queryset(base, request.user)
    current_shows = list(base.filter(start__lte=today, end__gte=today).order_by('-start'))
    future_shows = list(base.filter(start__gt=today).order_by('start'))
    past_shows = list(base.filter(end__lt=today).order_by('-start'))

    hero_show = current_shows[0] if current_shows else (future_shows[0] if future_shows else None)
    hero_is_current = bool(current_shows)
    display_future_shows = future_shows if hero_is_current else future_shows[1:]

    all_shows = current_shows + future_shows + past_shows
    manageable_show_ids = {s.id for s in all_shows if can_manage_show(request.user, s)}
    deletable_show_ids = {s.id for s in all_shows if can_delete_show(request.user, s)}

    return render(request, 'public/index.html', {
        'hero_show': hero_show,
        'hero_is_current': hero_is_current,
        'current_shows': current_shows,
        'future_shows': display_future_shows,
        'past_shows': past_shows,
        'can_manage_show': manageable_show_ids,
        'can_delete_show': deletable_show_ids,
        'structured_data_json': dump_json_ld(schema_to_dict(gallery_to_schema(request))),
    })


def contact(request):
    return render(request, 'public/contact.html')


def visit(request):
    return render(request, 'public/visit.html')


def about(request):
    return render(request, 'public/about.html')


def linktree(request):
    today = dt.date.today()
    current_shows = list(
        Show.objects.filter(
            status=Show.STATUS_PUBLISHED,
            start__lte=today,
            end__gte=today,
        ).order_by('-start')
    )
    open_call_shows = list(
        Show.objects.filter(
            status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_OPEN,
        ).order_by('start')
    )
    custom_links = list(LinkTreeEntry.objects.filter(is_active=True))
    return render(request, 'public/linktree.html', {
        'current_shows': current_shows,
        'open_call_shows': open_call_shows,
        'custom_links': custom_links,
    })


def howto(request):
    active_role_keys = []

    if request.user.is_authenticated:
        if is_staff_user(request.user):
            active_role_keys.append('staff')
        elif is_curator_user(request.user):
            active_role_keys.append('curator')
        elif is_juror_user(request.user):
            active_role_keys.append('juror')
        else:
            active_role_keys.append('artist')

    role_guides = [ROLE_DOCUMENTATION[key] for key in active_role_keys if key in ROLE_DOCUMENTATION]

    user_role = active_role_keys[0] if active_role_keys else None
    visible_how_tos = [
        g for g in HOW_TO_GUIDES
        if (g['roles'] is None and not (g.get('public_only') and user_role))
        or (user_role and g['roles'] and user_role in g['roles'])
    ]

    context = {
        'how_to_guides': visible_how_tos,
        'general_guide': GENERAL_GUIDE,
        'role_guides': role_guides,
        'active_roles': active_role_keys,
    }
    return render(request, 'public/howto.html', context)
