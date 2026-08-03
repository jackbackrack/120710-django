"""The endpoint Railway asks "are you ready to take traffic?".

Deliberately does nothing: no database, no cache, no storage. It answers the only question
the deploy needs answered — has this container finished starting and can it serve a
request — and it has to keep answering during the seconds when the new container is up and
the old one is being drained.

A health check that touched the database would conflate two different failures. A brief
database blip would make Railway conclude the new build is broken and roll back a deploy
that was fine, and during a deploy that is the worst possible time to be wrong about it.

Not cached, and never redirected: a 301 to a canonical host would fail the check.
"""
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@require_safe
@never_cache
def healthz(request):
    return HttpResponse('ok', content_type='text/plain')
