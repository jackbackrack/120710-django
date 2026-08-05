"""The consignment agreement: one page an artist signs, and the staff view of who has.

The artist-facing page is reachable with a signed token as well as by logging in, following
the pattern RSVPs, visits and campaigns already use. Requiring an account here would block
the artists who most need to complete it — someone whose profile a curator created for them,
or an estate signing on behalf of an artist who has died.

See docs/consignment-agreements.md.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from gallery import consignment as terms
from gallery.consignment_mail import send_signed_copy
from gallery.models import Artist, Consignment, Show
from gallery.models.consignment import fingerprint_of
from gallery.permissions import can_manage_show, can_view_consignment

SIGN_SALT = 'gallery.consignment.sign'


def sign_token(show, artist):
    """A link that opens one artist's agreement for one show.

    The email is in the token as well as the ids so that a recycled primary key cannot open
    somebody else's agreement — the same guard `visits.cancel_token` uses.
    """
    return signing.dumps({'show': show.pk, 'artist': artist.pk, 'email': artist.email},
                         salt=SIGN_SALT)


def from_token(token, max_age=None):
    try:
        data = signing.loads(token, salt=SIGN_SALT, max_age=max_age)
    except signing.BadSignature:
        return None, None
    show = Show.objects.filter(pk=data.get('show')).prefetch_related('sites').first()
    artist = Artist.objects.filter(pk=data.get('artist')).first()
    if show is None or artist is None or artist.email != data.get('email'):
        return None, None
    return show, artist


def sign_url(show, artist, request=None):
    path = reverse('gallery:consign_with_token',
                   kwargs={'slug': show.slug, 'token': sign_token(show, artist)})
    return request.build_absolute_uri(path) if request is not None else path


def _artist_for(request, show, token):
    """Who this page is for: the token's artist, or the signed-in user's own profile.

    Staff cannot open somebody's signing page by URL. Signing is an act by a person, and a
    page that let a curator sign as an artist would make every signature deniable.
    """
    if token:
        tok_show, artist = from_token(token)
        if artist is None or tok_show is None or tok_show.pk != show.pk:
            raise Http404
        return artist
    if not request.user.is_authenticated:
        raise Http404
    artist = (Artist.objects.filter(user=request.user, artworks__shows=show)
              .distinct().first())
    if artist is None:
        raise Http404
    return artist


def _page(request, show, artist, token):
    try:
        rate = terms.commission_rate_for(show)
    except terms.NoCommissionRate as exc:
        return render(request, 'gallery/consign_unavailable.html',
                      {'show': show, 'artist': artist, 'reason': str(exc),
                       'is_staff_reason': True}, status=409)

    rows = terms.artwork_rows(show, artist, rate)
    blocking = terms.blockers(show, artist, rows=rows)
    signed = (Consignment.objects
              .filter(show=show, artist=artist, status=Consignment.STATUS_SIGNED)
              .order_by('-version').first())

    return render(request, 'gallery/consign.html', {
        'show': show,
        'artist': artist,
        'token': token,
        'rate': rate,
        'rows': rows,
        'blockers': blocking,
        'fatal': any(b.get('fatal') for b in blocking),
        'can_sign': not blocking,
        'signed': signed,
        'out_of_date': terms.is_out_of_date(signed) if signed else False,
        'sections': terms.terms_text(rate),
        'custody': terms.custody_for(show),
        'total_agreed_value': sum(r['agreed_value'] or 0 for r in rows),
    })


def consign(request, slug, token=None):
    show = get_object_or_404(Show.objects.prefetch_related('sites'), slug=slug)
    artist = _artist_for(request, show, token)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save':
            _save_inline(request, show, artist)
            # Land on the signature rather than the top of the page. Saving is the step that
            # reveals it, and a redirect to the top looks like nothing happened.
            return redirect(_back(show, token) + '#sign')
        if action == 'sign':
            return _sign(request, show, artist, token)
    return _page(request, show, artist, token)


def _back(show, token):
    if token:
        return reverse('gallery:consign_with_token',
                       kwargs={'slug': show.slug, 'token': token})
    return reverse('gallery:consign', kwargs={'slug': show.slug})


def _save_inline(request, show, artist):
    """Take the address, the representation answer and the agreed values from the page.

    All of it saves here rather than on the profile and artwork forms, because the whole
    point of the page is that an artist does not have to visit three of them.
    """
    changed = []
    for field in ('street', 'city', 'state'):
        if field in request.POST:
            setattr(artist, field, request.POST.get(field, '').strip())
            changed.append(field)

    represented = request.POST.get('is_represented')
    if represented in ('yes', 'no'):
        artist.is_represented = represented == 'yes'
        artist.representing_gallery = (request.POST.get('representing_gallery', '').strip()
                                       if artist.is_represented else '')
        changed += ['is_represented', 'representing_gallery']
    if changed:
        artist.save(update_fields=list(dict.fromkeys(changed)))

    for artwork in terms.artworks_for(show, artist):
        raw = request.POST.get(f'agreed_value_{artwork.pk}')
        if raw is None:
            continue
        raw = raw.replace(',', '').replace('$', '').strip()
        if raw == '':
            continue
        try:
            value = float(raw)
        except ValueError:
            messages.error(request, f'“{raw}” is not an amount — {artwork.name} not saved.')
            continue
        if value < 0:
            messages.error(request, f'{artwork.name}: an agreed value cannot be negative.')
            continue
        # Refused, not silently clamped. Quietly changing a number somebody typed into a
        # document they are about to sign is worse than telling them it will not do.
        if terms.too_high(artwork.price, value):
            messages.error(
                request,
                f'{artwork.name}: an agreed value cannot be more than the asking price of '
                f'${artwork.price:,.0f}. Lower it, or raise the price of the piece.')
            continue
        if artwork.agreed_value != value:
            artwork.agreed_value = value
            artwork.save(update_fields=['agreed_value'])
    messages.success(request, 'Saved.')


@transaction.atomic
def _sign(request, show, artist, token):
    if not terms.can_sign(show, artist):
        messages.error(request, 'There is still something to fill in before signing.')
        return redirect(_back(show, token))

    typed = request.POST.get('signed_name', '').strip()
    if not typed:
        messages.error(request, 'Type your name to sign.')
        return redirect(_back(show, token))
    if not request.POST.get('agree'):
        messages.error(request, 'Tick the box to confirm you agree.')
        return redirect(_back(show, token))

    now = timezone.now()
    snapshot = terms.freeze(show, artist, at=now)
    rate = terms.commission_rate_for(show)
    rows = snapshot['artworks']

    previous = (Consignment.objects.select_for_update()
                .filter(show=show, artist=artist).order_by('-version').first())
    if previous and previous.is_signed:
        previous.status = Consignment.STATUS_SUPERSEDED
        previous.save(update_fields=['status'])

    consignment = Consignment.objects.create(
        show=show, artist=artist,
        version=(previous.version + 1) if previous else 1,
        status=Consignment.STATUS_SIGNED,
        commission_rate=rate,
        snapshot=snapshot,
        fingerprint=fingerprint_of(terms.material_facts(show, artist, rate, rows)),
        signed_at=now,
        signed_name=typed,
        signed_ip=_client_ip(request),
        signed_user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        signed_by=request.user if request.user.is_authenticated else None,
    )
    # After the row is committed, not inside the transaction: a mail server being slow or
    # down must not roll back a signature the artist has already given.
    transaction.on_commit(lambda: send_signed_copy(consignment, request=request))
    messages.success(request, 'Signed — thank you. A copy is on its way to you by email.')
    return redirect(_back(show, token))


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


@require_safe
@login_required
def consignment_pdf(request, pk):
    """Rendered on demand, never stored.

    Media is world-readable — AWS_DEFAULT_ACL is 'public-read' and AWS_QUERYSTRING_AUTH is
    off — so a signed agreement, which carries a home address and a signature, cannot be
    written there. Rendering from the snapshot behind a permission check means there is no
    file to leak, and the output is identical every time because the snapshot cannot change.
    """
    from gallery.consignment_pdf import render_consignment

    consignment = get_object_or_404(
        Consignment.objects.select_related('artist', 'show'), pk=pk)
    if not can_view_consignment(request.user, consignment):
        raise Http404
    pdf = render_consignment(consignment)
    response = HttpResponse(pdf, content_type='application/pdf')
    name = f'consignment-{consignment.show.slug}-{consignment.artist.pk}-v{consignment.version}.pdf'
    response['Content-Disposition'] = f'inline; filename="{name}"'
    return response


@login_required
def show_consignments(request, slug):
    """Staff view: who has signed, what is outstanding, and the gallery's total exposure."""
    show = get_object_or_404(Show.objects.prefetch_related('sites'), slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    try:
        rate = terms.commission_rate_for(show)
        rate_error = None
    except terms.NoCommissionRate as exc:
        rate, rate_error = None, str(exc)

    signed_by_artist = {}
    for c in (Consignment.objects.filter(show=show)
              .exclude(status=Consignment.STATUS_SUPERSEDED)
              .select_related('artist')):
        signed_by_artist[c.artist_id] = c

    rows, exposure, outstanding = [], 0, 0
    for artist in Artist.objects.filter(artworks__shows=show).distinct().order_by('name'):
        works = terms.artwork_rows(show, artist, rate)
        consigned = signed_by_artist.get(artist.pk)
        value = sum(w['agreed_value'] or 0 for w in works)
        exposure += value
        blocking = terms.blockers(show, artist, rows=works)
        if not consigned or not consigned.is_signed:
            outstanding += 1
        outliers = [w for w in works if terms.is_outlier(w)]
        rows.append({
            'artist': artist,
            'artworks': works,
            'agreed_value': value,
            'outliers': outliers,
            'missing_values': sum(1 for w in works if w['agreed_value'] is None),
            'consignment': consigned,
            'out_of_date': terms.is_out_of_date(consigned) if consigned else False,
            'represented': artist.is_represented,
            'blockers': blocking,
            'sign_url': sign_url(show, artist, request),
        })

    return render(request, 'gallery/show_consignments.html', {
        'show': show, 'rows': rows, 'rate': rate, 'rate_error': rate_error,
        'exposure': exposure, 'outstanding': outstanding,
    })


@require_POST
@login_required
def email_consignment_links(request, slug):
    """Send every unsigned artist their own signing link."""
    from gallery.consignment_mail import send_consignment_requests

    show = get_object_or_404(Show.objects.prefetch_related('sites'), slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    only = request.POST.get('email') or None
    sent = send_consignment_requests(show, request=request, only_email=only)
    messages.success(request, f'Sent {sent} consignment request{"" if sent == 1 else "s"}.')
    return redirect('gallery:show_consignments', slug=show.slug)
