"""Asking an artist to sign, and sending them their copy once they have.

Two different mails. The request carries a signing link and no attachment; the receipt
carries the PDF, because that is the one distribution that needs no permission check — it
goes to the person the document is about.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from gallery import consignment as terms
from gallery.models import Artist, Consignment

logger = logging.getLogger(__name__)


def _venue(show):
    sites = list(show.sites.all())
    return sites[0] if len(sites) == 1 else None


def unsigned_artists(show):
    """Artists with work in the show who have no current signed agreement.

    Represented artists are skipped: their consignment is arranged with their gallery, so
    chasing them for a signature they cannot validly give would be both useless and
    confusing.
    """
    signed = set(
        Consignment.objects.filter(show=show, status=Consignment.STATUS_SIGNED)
        .values_list('artist_id', flat=True))
    out = []
    for artist in Artist.objects.filter(artworks__shows=show).distinct().order_by('name'):
        if artist.is_represented:
            continue
        if artist.pk in signed:
            current = (Consignment.objects
                       .filter(show=show, artist=artist,
                               status=Consignment.STATUS_SIGNED)
                       .order_by('-version').first())
            if not terms.is_out_of_date(current):
                continue
        if not artist.email:
            continue
        out.append(artist)
    return out


def send_consignment_requests(show, request=None, only_email=None):
    """Mail each unsigned artist their own signing link. Returns how many went."""
    from gallery.views.consignment import sign_url

    try:
        rate = terms.commission_rate_for(show)
    except terms.NoCommissionRate:
        logger.warning('Not sending consignment requests for %s: no commission rate', show)
        return 0

    sent = 0
    for artist in unsigned_artists(show):
        if only_email and artist.email.lower() != only_email.lower():
            continue
        rows = terms.artwork_rows(show, artist, rate)
        html = render_to_string('email/consignment_request.html', {
            'artist': artist, 'show': show, 'site': _venue(show), 'rate': rate,
            'artworks': rows, 'sign_url': sign_url(show, artist, request),
            'blockers': terms.blockers(show, artist, rows=rows),
        }, request=request)
        message = EmailMultiAlternatives(
            subject=f'{show.name}: please sign your consignment agreement',
            body=strip_tags(html),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[artist.email],
            reply_to=([request.user.email]
                      if request is not None and request.user.is_authenticated
                      and request.user.email else None),
        )
        message.attach_alternative(html, 'text/html')
        try:
            # Not fail_silently. A consignment request that never arrived must not look
            # exactly like one that did — the artist turns up with work and no agreement.
            message.send()
        except Exception as exc:                                  # noqa: BLE001
            logger.exception('Consignment request to %s failed: %s', artist.email, exc)
            continue
        sent += 1
    return sent


def send_signed_copy(consignment, request=None):
    """The artist's own copy, with the PDF attached."""
    from gallery.consignment_pdf import render_consignment

    artist = consignment.artist
    if not artist.email:
        return False
    show = consignment.show
    html = render_to_string('email/consignment_signed.html', {
        'artist': artist, 'show': show, 'site': _venue(show),
        'consignment': consignment, 'snapshot': consignment.snapshot,
    }, request=request)
    message = EmailMultiAlternatives(
        subject=f'{show.name}: your signed consignment agreement',
        body=strip_tags(html),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[artist.email],
    )
    message.attach_alternative(html, 'text/html')
    message.attach(
        f'consignment-{show.slug}-v{consignment.version}.pdf',
        render_consignment(consignment), 'application/pdf')
    try:
        message.send()
    except Exception as exc:                                      # noqa: BLE001
        logger.exception('Signed consignment copy to %s failed: %s', artist.email, exc)
        return False
    return True
