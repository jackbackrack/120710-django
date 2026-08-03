"""What happens when a POST is rejected for a missing or invalid CSRF token.

Django's default page says "CSRF verification failed. Request aborted." — accurate, and
useless to the person reading it, who has just lost a form they spent ten minutes filling
in and has no idea whether the fault is theirs, ours, or their browser's.

The logging is the more important half. A CSRF rejection has several causes that look
identical from outside, and the one distinguishing test can only be made here, in the
failure handler, by trying to read the body:

  * `UnreadablePostError` — the body died in transit. The client or something between it
    and us stopped sending partway. Django's own middleware swallows this exact error and
    falls through to "token missing" (see django/middleware/csrf.py), which is why a
    broken upload and a genuinely absent token are indistinguishable in the log line.
  * an empty POST — the request arrived carrying nothing at all, which points upstream:
    a proxy or WAF that dropped the body rather than a browser that mis-sent it.
  * a populated POST with no `csrfmiddlewaretoken` — the page was rendered or served
    without the hidden field, so the fault is ours or a cache's.
  * a populated POST *with* a token — the token was stale or mismatched, which is the
    ordinary case: a page left open too long, or a session that rotated underneath it.

**Key names only, never values.** The form that most often lands here is an artist
profile, and it carries a bio, a phone number, an email address and a postal address.
Names are enough to tell the four cases apart; values would put personal data in the log
of every failed submission.

Defensive throughout: this runs when something is already wrong, and a handler that
raises turns somebody's 403 into a 500.
"""
import logging

from django.http import HttpResponse, UnreadablePostError
from django.shortcuts import render
from django.template import TemplateDoesNotExist

logger = logging.getLogger(__name__)


# What the body turned out to be. The page says different things for each, because they
# are different problems for the person reading it — a stale token means "reload and try
# again", an empty body means "your browser sent nothing, and here is the usual reason".
BODY_UNREADABLE = 'unreadable'
BODY_EMPTY = 'empty'
BODY_STALE = 'stale'
BODY_NO_TOKEN = 'no-token'


def _body_shape(request):
    """(kind, phrase) — what the request actually carried.

    Reading `request.POST` is the whole point and is also the thing most likely to blow
    up, so every branch is guarded.
    """
    try:
        keys = sorted(request.POST.keys())
    except UnreadablePostError:
        return BODY_UNREADABLE, 'unreadable (connection broke before the body finished)'
    except Exception as exc:                                  # noqa: BLE001 — see docstring
        return BODY_UNREADABLE, f'unreadable ({type(exc).__name__})'
    if not keys:
        return BODY_EMPTY, 'empty (arrived with no fields at all)'
    if 'csrfmiddlewaretoken' in keys:
        return BODY_STALE, (f'{len(keys)} fields including the token '
                            f'(so it was stale, not absent): {keys}')
    return BODY_NO_TOKEN, f'{len(keys)} fields but no token: {keys}'


def _edge(request):
    """What the CDN in front of us saw, so a rejection can be traced past our own logs.

    `cf-ray` identifies one request in Cloudflare's Security Events. That is the only way
    to answer "did Cloudflare touch this?" — the alternative, when a body arrives empty,
    is guessing between the visitor's machine and our own edge configuration, and we have
    already lost days to exactly that guess.

    `cf-connecting-ip` is the visitor's real address; without it every request looks like
    it came from the proxy, so "is this one person or everyone?" is unanswerable too.
    """
    ray = request.META.get('HTTP_CF_RAY')
    if not ray:
        return 'no CDN headers (direct, or not behind Cloudflare)'
    return (f'cf-ray={ray} '
            f'ip={request.META.get("HTTP_CF_CONNECTING_IP", "?")} '
            f'country={request.META.get("HTTP_CF_IPCOUNTRY", "?")}')


def csrf_failure(request, reason=''):
    try:
        user = request.user if hasattr(request, 'user') else None
        who = f'{user.pk} <{user.email}>' if user is not None and user.is_authenticated \
            else 'signed out'
        logger.warning(
            'CSRF rejected: %s | path=%s | user=%s | body=%s | '
            'csrftoken cookie=%s | content_type=%s | content_length=%s | %s | ua=%s',
            reason or 'no reason given',
            request.path,
            who,
            _body_shape(request)[1],
            'present' if request.COOKIES.get('csrftoken') else 'ABSENT',
            request.META.get('CONTENT_TYPE', '?'),
            request.META.get('CONTENT_LENGTH', '?'),
            _edge(request),
            request.META.get('HTTP_USER_AGENT', '?')[:200],
        )
    except Exception:                                         # noqa: BLE001
        # Diagnostics must never be the reason somebody sees a 500 instead of a 403.
        logger.exception('CSRF failure handler could not log the request')

    try:
        kind, _phrase = _body_shape(request)
    except Exception:                                         # noqa: BLE001
        kind = None
    # Only call it a file problem when a file was actually being sent. An empty
    # urlencoded post is the same symptom with a different cause, and telling somebody to
    # re-download a photo they never attached would be worse than saying nothing.
    sent_a_file = 'multipart/form-data' in request.META.get('CONTENT_TYPE', '')

    try:
        return render(request, '403_csrf.html', {
            'reason': reason,
            'body_empty': kind in (BODY_EMPTY, BODY_UNREADABLE),
            'sent_a_file': sent_a_file,
        }, status=403)
    except TemplateDoesNotExist:
        return HttpResponse('Your submission could not be verified. Please go back, '
                            'reload the page, and try again.', status=403)
