"""Staff view of who is coming, and the calendar feed they can subscribe to.

The list is read-only on purpose. A visit is the visitor's to change — they have a cancellation
link — and a gallery that silently cancels somebody's booking without telling them is worse than
one that emails to say sorry.
"""
import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_POST, require_safe

from gallery import calendars
from gallery.calendars import site_timezone
from gallery.models import Site, Visit
from gallery.permissions import is_staff_user


def _may_see(user):
    return is_staff_user(user) or user.groups.filter(name='curator').exists()


@login_required
def visit_list(request):
    if not _may_see(request.user):
        raise Http404

    now = dj_timezone.now()
    upcoming = (Visit.objects.filter(when__gte=now, cancelled_at__isnull=True)
                .select_related('site'))
    recent = (Visit.objects.filter(when__lt=now).select_related('site')
              .order_by('-when')[:25])

    feeds = []
    if is_staff_user(request.user):
        for site in Site.objects.filter(visits_enabled=True).order_by('name'):
            if not site.visit_feed_token:
                site.save(update_fields=['visit_feed_token'])
            feeds.append((site, request.build_absolute_uri(
                f'/visits/{site.visit_feed_token}.ics')))

    return render(request, 'gallery/visit_list.html', {
        'upcoming': [(v, v.when.astimezone(site_timezone(v.site))) for v in upcoming],
        'recent': [(v, v.when.astimezone(site_timezone(v.site))) for v in recent],
        'feeds': feeds,
    })


@require_safe
def visits_ics(request, token):
    """The visits feed, found only by its secret.

    Unauthenticated because a subscribed calendar cannot sign in — Google fetches this with no
    cookies and no headers of ours — so the URL *is* the credential. It carries visitors' names
    and email addresses, which is why it is a random token rather than a slug, why it is never
    linked from a public page, and why it can be regenerated.

    A wrong token is a 404 rather than a 403: there is nothing to be gained by confirming that a
    visits feed exists at all.
    """
    site = Site.objects.filter(visit_feed_token=token).first() if token else None
    if site is None or not site.visits_enabled:
        raise Http404

    now = dj_timezone.now()
    visits = (Visit.objects
              .filter(site=site, cancelled_at__isnull=True,
                      when__gte=now - dt.timedelta(days=60))
              .order_by('when'))
    body = calendars.visits_feed(
        site, visits, domain=request.get_host().split(':')[0],
        url=request.build_absolute_uri())
    response = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    # Inline, not an attachment: a Content-Disposition here makes most clients save a dead
    # snapshot instead of subscribing.
    response['Content-Disposition'] = 'inline; filename="visits.ics"'
    # Never let a shared cache hold a calendar of people's names and addresses.
    response['Cache-Control'] = 'private, max-age=300'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@login_required
@require_POST
def regenerate_visit_feed(request, pk):
    """Change the secret, which is the only way to deal with a URL that has got out."""
    if not is_staff_user(request.user):
        raise Http404
    site = get_object_or_404(Site, pk=pk)
    site.new_visit_feed_token()
    site.save(update_fields=['visit_feed_token'])
    messages.success(request, f'New calendar address for {site.name}. The old one has stopped '
                              f'working — subscribe again with the new one.')
    return redirect('gallery:visit_list')
