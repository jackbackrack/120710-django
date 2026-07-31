"""Staff view of who is coming.

Read-only on purpose. A visit is the visitor's to change — they have a cancellation link — and a
gallery that silently cancels somebody's booking without telling them is worse than one that
emails to say sorry.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone as dj_timezone

from gallery.calendars import site_timezone
from gallery.models import Visit
from gallery.permissions import is_staff_user


@login_required
def visit_list(request):
    if not (is_staff_user(request.user) or request.user.groups.filter(name='curator').exists()):
        raise Http404

    now = dj_timezone.now()
    upcoming = (Visit.objects.filter(when__gte=now, cancelled_at__isnull=True)
                .select_related('site'))
    recent = (Visit.objects.filter(when__lt=now).select_related('site')
              .order_by('-when')[:25])
    return render(request, 'gallery/visit_list.html', {
        'upcoming': [(v, v.when.astimezone(site_timezone(v.site))) for v in upcoming],
        'recent': [(v, v.when.astimezone(site_timezone(v.site))) for v in recent],
    })
