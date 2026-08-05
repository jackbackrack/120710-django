"""Working out what a consignment agreement says, and freezing it when it is signed.

Kept out of the views because three things need the same answers and must not disagree: the
page the artist signs, the PDF rendered afterwards, and the staff dashboard that totals what
the gallery is liable for.

See docs/consignment-agreements.md for the reasoning.
"""
from decimal import Decimal

from django.utils import timezone

from gallery.models.consignment import TERMS_VERSION, fingerprint_of


class NoCommissionRate(Exception):
    """No rate could be determined, so no agreement may be generated.

    Raised rather than defaulting to zero. A contract is the wrong place to discover that
    nobody decided what the gallery takes.
    """


def commission_rate_for(show):
    """The rate this show sells at, as a percentage.

    The show's own rate wins when set — that is what an override is for. Otherwise the
    venue's. A show at two venues that disagree has no answer, and guessing would put a
    number nobody chose into a signed document.
    """
    if show.commission_rate is not None:
        return show.commission_rate

    rates = {s.commission_rate for s in show.sites.all() if s.commission_rate is not None}
    sites_count = show.sites.count()
    if len(rates) == 1 and sites_count == len(rates):
        return rates.pop()
    if not rates:
        raise NoCommissionRate(
            f'No commission rate is set for {show.name} or its venue. Set one on the venue, '
            f'or on the show if it is on different terms.')
    raise NoCommissionRate(
        f'{show.name} is at venues with different commission rates. Set a rate on the show '
        f'itself to say which applies.')


def agreed_value_for(artwork):
    """What the gallery would pay if this piece were lost, stolen or damaged in its care.

    The artist's stated figure when they have given one, otherwise the retail price. The
    price is the default rather than the other way round because "replacement cost" reads to
    anyone as materials-and-labour, and a figure anchored on the asking price errs high
    instead of low. Works that are not for sale have no price to inherit, so those are the
    ones an artist has to fill in.
    """
    if artwork.replacement_cost is not None:
        return Decimal(str(artwork.replacement_cost))
    if artwork.price is not None:
        return Decimal(str(artwork.price))
    return None


def artworks_for(show, artist):
    """The artist's pieces in this show, in the order the agreement lists them."""
    return (artist.artworks.filter(shows=show)
            .prefetch_related('artists')
            .order_by('name', 'pk'))


def _money(value):
    return None if value is None else float(value)


def artwork_rows(show, artist, rate):
    """One row per piece: what it is, what it is worth, and who gets what if it sells."""
    rows = []
    for artwork in artworks_for(show, artist):
        agreed = agreed_value_for(artwork)
        price = Decimal(str(artwork.price)) if artwork.price is not None else None
        gallery_share = artist_share = None
        if price is not None and rate is not None:
            gallery_share = (price * Decimal(rate) / Decimal(100)).quantize(Decimal('0.01'))
            artist_share = price - gallery_share
        rows.append({
            'pk': artwork.pk,
            'title': artwork.name,
            'year': artwork.end_year,
            'medium': artwork.medium or '',
            'dimensions': artwork.formatted_dimensions or '',
            'for_sale': artwork.pricing_type == artwork.PRICING_FOR_SALE,
            'pricing': artwork.get_pricing_type_display(),
            'price': _money(price),
            'agreed_value': _money(agreed),
            'gallery_share': _money(gallery_share),
            'artist_share': _money(artist_share),
        })
    return rows


def blockers(show, artist, rows=None):
    """What stops this artist signing, in the order the page asks for it.

    Only three things, deliberately. Every one of them is fixable on the consignment page
    itself — sending somebody to the profile form and then to each artwork form turns this
    into a three-stop errand, and that is where people give up.
    """
    found = []
    if artist.is_represented is None:
        found.append({
            'key': 'representation',
            'text': 'Say whether another gallery represents you.',
        })
    elif artist.is_represented:
        found.append({
            'key': 'represented',
            'text': f'{artist.representing_gallery or "Your gallery"} represents you, so we '
                    f'arrange this with them rather than with you.',
            'fatal': True,
        })

    missing_address = [f for f in ('street', 'city', 'state') if not getattr(artist, f)]
    if missing_address:
        found.append({
            'key': 'address',
            'text': 'Add your address, so we know where to return unsold work.',
        })

    rows = artwork_rows(show, artist, None) if rows is None else rows
    if not rows:
        found.append({
            'key': 'no_artworks',
            'text': 'You have no work in this show yet.',
            'fatal': True,
        })
    elif any(r['agreed_value'] is None for r in rows):
        found.append({
            'key': 'agreed_value',
            'text': 'Set an agreed value for every piece.',
        })
    return found


def can_sign(show, artist, rows=None):
    return not blockers(show, artist, rows=rows)


def material_facts(show, artist, rate, rows):
    """The parts of an agreement that, if they change, make a signed one out of date.

    Only the terms — not display niceties. Re-asking an artist to sign because a medium was
    re-typed with different capitalisation would train them to click through it.
    """
    return {
        'artist': artist.pk,
        'show': show.pk,
        'rate': str(rate) if rate is not None else None,
        'terms_version': TERMS_VERSION,
        'artworks': sorted(
            [{'pk': r['pk'], 'price': r['price'], 'agreed_value': r['agreed_value']}
             for r in rows],
            key=lambda r: r['pk']),
    }


def freeze(show, artist, at=None):
    """Everything the agreement says, as a plain dict, at this moment.

    `at` is passed in by the signing view so the snapshot's `frozen_at` and the row's
    `signed_at` are the same instant rather than two calls to the clock a few microseconds
    apart — the document states a time, and it should be the time on the record.

    Stored on the Consignment at signing and read straight back out to render the PDF. Once
    written it is the agreement: nothing outside it is consulted again, so nothing outside it
    can change what somebody signed.
    """
    rate = commission_rate_for(show)
    rows = artwork_rows(show, artist, rate)
    sites = list(show.sites.all())
    venue = sites[0] if len(sites) == 1 else None
    windows = list(show.schedule_windows.all())
    dropoffs = [w.date for w in windows if w.kind != 'pickup']
    pickups = [w.date for w in windows if w.kind == 'pickup']

    at = at or timezone.now()
    return {
        'frozen_at': at.isoformat(),
        'terms_version': TERMS_VERSION,
        'artist': {
            'name': str(artist),
            'email': artist.email,
            'street': artist.street,
            'city': artist.city,
            'state': artist.state,
            'zipcode': artist.zipcode,
            'country': str(artist.country),
        },
        'venue': {
            'name': venue.name if venue else '',
            'street': venue.street if venue else '',
            'city': venue.city if venue else '',
            'state': venue.state if venue else '',
            'postal_code': venue.postal_code if venue else '',
        },
        'show': {
            'name': show.name,
            'slug': show.slug,
            'start': show.start.isoformat() if show.start else None,
            'end': show.end.isoformat() if show.end else None,
            'curators': [str(c) for c in show.ordered_curators],
        },
        'custody': {
            'from': min(dropoffs).isoformat() if dropoffs else None,
            'to': max(pickups).isoformat() if pickups else None,
        },
        'commission_rate': str(rate),
        'artworks': rows,
        'total_agreed_value': sum(r['agreed_value'] or 0 for r in rows),
        'terms': terms_text(),
    }


def current_fingerprint(show, artist):
    """Digest of today's facts, to compare against what a signed agreement covers."""
    rate = commission_rate_for(show)
    return fingerprint_of(material_facts(show, artist, rate, artwork_rows(show, artist, rate)))


def snapshot_fingerprint(consignment):
    snap = consignment.snapshot
    rows = snap.get('artworks', [])
    return fingerprint_of({
        'artist': consignment.artist_id,
        'show': consignment.show_id,
        'rate': snap.get('commission_rate'),
        'terms_version': snap.get('terms_version'),
        'artworks': sorted(
            [{'pk': r['pk'], 'price': r['price'], 'agreed_value': r['agreed_value']}
             for r in rows],
            key=lambda r: r['pk']),
    })


def is_out_of_date(consignment):
    """Whether the work in the show has moved on from what was signed.

    An agreement covering three pieces while five are on the wall is a document that will be
    produced in the one situation where it matters and found not to cover the piece in
    question. Better to notice and re-sign.
    """
    if not consignment.is_signed:
        return False
    try:
        return current_fingerprint(consignment.show, consignment.artist) != consignment.fingerprint
    except NoCommissionRate:
        return False


def terms_text():
    """The boilerplate, versioned.

    Kept in code rather than the database so it is reviewed like code and its history is in
    git. Signed agreements store the rendered text, so editing this never alters what
    somebody already signed; changing it in a way that matters means bumping TERMS_VERSION.
    """
    return [
        ('Title and ownership', [
            'The artist consigns the works listed above to the gallery for exhibition, and '
            'for sale where the work is marked for sale.',
            'The artist confirms they made the work, own it, and are free to consign it.',
        ]),
        ('While we have it', [
            'The gallery is responsible for the work from the time it is dropped off until '
            'it is collected. The artist is responsible for getting it here, and the buyer '
            'for taking it away.',
            'If a piece is lost, stolen or damaged beyond repair while in the gallery’s '
            'care, the gallery pays the artist that piece’s agreed value in full — not the '
            'agreed value less commission.',
            'The gallery will not alter, copy or lend the work, and will take reasonable '
            'care of it.',
        ]),
        ('If it sells', [
            'The gallery keeps the commission shown above; the artist receives the rest.',
            'The artist is paid within 30 days of the gallery being paid.',
            'The work is not sold above or below the listed price without asking the artist '
            'first.',
        ]),
        ('Copyright', [
            'The artist keeps copyright in the work. Selling a piece transfers the object, '
            'not the right to reproduce it.',
            'The gallery may photograph the work to document and promote the exhibition.',
        ]),
        ('Getting it back', [
            'Unsold work is returned to the artist at the end of the show, at the pickup '
            'time they choose.',
        ]),
    ]
