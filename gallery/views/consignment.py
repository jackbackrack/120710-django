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
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from gallery import consignment as terms
from gallery import us_states
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

    Whoever holds a link can sign with it — that is true of every e-signature link and is
    the deliberate price of reaching artists with no account. But this page also hands staff
    that link on a "copy link" button, so a curator could have opened an artist's page and
    signed as them, which would make every signature deniable. A logged-in person who runs
    the show is therefore refused somebody else's token.

    It is not airtight and cannot be: the same curator in a private window is
    indistinguishable from the artist. What that leaves behind is the audit trail —
    `signed_by` records whoever was logged in when a signature was made, and the dashboard
    and the PDF say so when it was not the artist.
    """
    if token:
        tok_show, artist = from_token(token)
        if artist is None or tok_show is None or tok_show.pk != show.pk:
            raise Http404
        if (request.user.is_authenticated and artist.user_id != request.user.id
                and can_manage_show(request.user, show)):
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
    custody = terms.custody_for(show)
    blocking = terms.blockers(show, artist, rows=rows)
    signed = (Consignment.objects
              .filter(show=show, artist=artist, status=Consignment.STATUS_SIGNED)
              .order_by('-version').first())

    # Which individual boxes are empty, so the page can mark them where they are rather
    # than only listing them in a summary further down.
    missing = {f for f in ('street', 'city', 'state') if not getattr(artist, f)}
    if artist.is_represented is None:
        missing.add('representation')
    missing_values = {r['pk'] for r in rows if r['agreed_value'] is None}

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
        'custody': custody,
        'custody_sentence': terms.custody_sentence(
            custody, date_format=lambda d: d.strftime('%-d %b %Y') if d else '—'),
        'missing': missing,
        'us_states_datalist': us_states.datalist_html(),
        'missing_values': missing_values,
        'total_agreed_value': sum(r['agreed_value'] or 0 for r in rows),
    })


def consign(request, slug, token=None):
    show = get_object_or_404(Show.objects.prefetch_related('sites'), slug=slug)
    artist = _artist_for(request, show, token)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save':
            refused = _save_inline(request, show, artist)
            # Land where the work is. A refusal goes to the top whatever else is true: the
            # messages render there, so jumping to the signature scrolls straight past the
            # explanation and the save reads as having silently changed the number.
            if refused:
                anchor = '#missing'
            else:
                anchor = '#sign' if terms.can_sign(show, artist) else '#missing'
            return redirect(_back(show, token) + anchor)
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

    Returns whether anything was refused, so the caller can land the reader on the
    explanation rather than past it.
    """
    refused = False
    changed = []
    for field in ('street', 'city'):
        if field in request.POST:
            setattr(artist, field, request.POST.get(field, '').strip())
            changed.append(field)

    # Normalised here too, not only in the artist and venue forms — this page writes to the
    # same column, and a third way in that skipped the check would put "ca" back.
    if 'state' in request.POST:
        try:
            artist.state = us_states.clean_state(request.POST['state'], artist.country)
            changed.append('state')
        except DjangoValidationError as exc:
            messages.error(request, exc.messages[0])
            refused = True

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
            refused = True
            continue
        if value < 0:
            messages.error(request, f'{artwork.name}: an agreed value cannot be negative.')
            refused = True
            continue
        # Refused, not silently clamped. Quietly changing a number somebody typed into a
        # document they are about to sign is worse than telling them it will not do — and
        # the message says what the value is *now*, because the box falls back to showing
        # the asking price, which otherwise reads as the form having quietly rewritten it.
        if terms.too_high(artwork.price, value):
            messages.error(
                request,
                f'{artwork.name}: ${value:,.0f} is more than the asking price of '
                f'${artwork.price:,.0f}, so it was not saved — the agreed value is still '
                f'${artwork.price:,.0f}. Lower it, or raise the price of the piece.')
            refused = True
            continue
        if artwork.agreed_value != value:
            artwork.agreed_value = value
            artwork.save(update_fields=['agreed_value'])
    if not refused:
        messages.success(request, 'Saved.')
    return refused


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
        terms_version=snapshot['terms_version'],
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
    # Back to where the button was. The confirmation renders there in place of the form, so
    # there is nothing to scroll to — and a fragment is still needed, because without one
    # the browser restores the old position and the page visibly jumps to the top first.
    return redirect(_back(show, token) + '#sign')


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
              .select_related('artist', 'voided_by')
              .order_by('version')):
        signed_by_artist[c.artist_id] = c

    rows, exposure, outstanding, unreachable = [], 0, 0, 0
    # Per artwork, not per row. A collaboration appears on every credited artist's row, so
    # summing the rows counted one $1,000 piece as $2,000 of liability — and the total is on
    # this page precisely so somebody can judge the real exposure before a show opens.
    counted = set()
    for artist in Artist.objects.filter(artworks__shows=show).distinct().order_by('name'):
        works = terms.artwork_rows(show, artist, rate)
        consigned = signed_by_artist.get(artist.pk)
        value = sum(w['agreed_value'] or 0 for w in works)
        exposure += sum(w['agreed_value'] or 0 for w in works
                        if w['pk'] not in counted)
        counted.update(w['pk'] for w in works)
        blocking = terms.blockers(show, artist, rows=works)
        # Two different numbers. `outstanding` is who still owes an agreement;
        # `unreachable` is how many of those have no address to send a link to. Counting
        # them together made the button offer to email 21 people and reach 5, which is the
        # kind of quiet shortfall nobody discovers until an artist turns up unsigned.
        needs_signing = (not consigned or not consigned.is_signed) and not artist.is_represented
        if needs_signing:
            if artist.email:
                outstanding += 1
            else:
                unreachable += 1
        outliers = [w for w in works if terms.is_outlier(w)]
        rows.append({
            'artist': artist,
            'artworks': works,
            'agreed_value': value,
            'outliers': outliers,
            'missing_values': sum(1 for w in works if w['agreed_value'] is None),
            'consignment': consigned,
            'out_of_date': terms.is_out_of_date(consigned) if consigned else False,
            'signed_by_someone_else': bool(
                consigned and consigned.is_signed and consigned.signed_by_id
                and consigned.signed_by_id != artist.user_id),
            'represented': artist.is_represented,
            'blockers': blocking,
            'sign_url': sign_url(show, artist, request),
        })

    return render(request, 'gallery/show_consignments.html', {
        'show': show, 'rows': rows, 'rate': rate, 'rate_error': rate_error,
        'exposure': exposure, 'outstanding': outstanding, 'unreachable': unreachable,
    })


@require_POST
@login_required
def void_consignment(request, pk):
    """Cancel a signed agreement, on the record.

    For whoever runs the show — its curators, staff, and the directors of its venue — which
    is the point: a site director manages this and has no Django admin access, so the only
    route to correcting a signed agreement cannot be one that requires it.

    Not a delete. The signed document is kept and stays readable, because what the artist
    agreed to remains a fact about what happened; voiding records that the gallery has since
    called it off, and who did so. The artist is then asked to sign a fresh one.
    """
    consignment = get_object_or_404(
        Consignment.objects.select_related('show', 'artist'), pk=pk)
    if not can_manage_show(request.user, consignment.show):
        raise Http404
    if not consignment.is_signed:
        messages.error(request, 'Only a signed agreement can be voided.')
        return redirect('gallery:show_consignments', slug=consignment.show.slug)

    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'Say why it is being voided — it goes on the record.')
        return redirect('gallery:show_consignments', slug=consignment.show.slug)

    consignment.status = Consignment.STATUS_VOIDED
    consignment.voided_at = timezone.now()
    consignment.voided_by = request.user
    consignment.void_reason = reason[:255]
    consignment.save(update_fields=['status', 'voided_at', 'voided_by', 'void_reason'])
    messages.success(
        request,
        f'{consignment.artist}\u2019s agreement is voided. They now show as unsigned, so '
        f'you can email them to sign a new one.')
    return redirect('gallery:show_consignments', slug=consignment.show.slug)


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
