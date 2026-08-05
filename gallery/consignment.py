"""Working out what a consignment agreement says, and freezing it when it is signed.

Kept out of the views because three things need the same answers and must not disagree: the
page the artist signs, the PDF rendered afterwards, and the staff dashboard that totals what
the gallery is liable for.

See docs/consignment-agreements.md for the reasoning.
"""
import datetime as dt
from decimal import Decimal

from django.utils import timezone

from gallery.models.consignment import TERMS_VERSION, fingerprint_of

# Used only when a show has no single venue to read it from — a show at two sites, which the
# rate check rejects anyway, so this is a floor rather than a policy.
DEFAULT_CUSTODY_GRACE_DAYS = 7


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
    if artwork.agreed_value is not None:
        return Decimal(str(artwork.agreed_value))
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
    # Each carries two wordings. `text` addresses the artist, who is being asked to do
    # something; `staff_text` describes them to somebody else. The dashboard was showing
    # "Add your address" to a curator reading about a third person.
    found = []
    if artist.is_represented is None:
        found.append({
            'key': 'representation',
            'text': 'Say whether another gallery represents you.',
            'staff_text': 'Has not said whether a gallery represents them.',
        })
    elif artist.is_represented:
        gallery = artist.representing_gallery or 'Another gallery'
        found.append({
            'key': 'represented',
            'text': f'{artist.representing_gallery or "Your gallery"} represents you, so we '
                    f'arrange this with them rather than with you.',
            'staff_text': f'{gallery} represents them — arranged with the gallery.',
            'fatal': True,
        })

    missing_address = [f for f in ('street', 'city', 'state') if not getattr(artist, f)]
    if missing_address:
        found.append({
            'key': 'address',
            'text': 'Add your address, so we know where to return unsold work.',
            'staff_text': 'No address on file to return unsold work to.',
        })

    rows = artwork_rows(show, artist, None) if rows is None else rows
    if not rows:
        found.append({
            'key': 'no_artworks',
            'text': 'You have no work in this show yet.',
            'staff_text': 'No work in this show.',
            'fatal': True,
        })
    elif any(r['agreed_value'] is None for r in rows):
        missing = sum(1 for r in rows if r['agreed_value'] is None)
        found.append({
            'key': 'agreed_value',
            'text': 'Set an agreed value for every piece.',
            'staff_text': f'{missing} piece{"" if missing == 1 else "s"} without an agreed '
                          f'value.',
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


def _logo_name(venue):
    """The venue's icon if it has one, else its main image. Either may be missing."""
    if venue is None:
        return ''
    for field in (venue.icon, venue.image):
        if field:
            return field.name
    return ''


def venue_of(show):
    """The one site a show is at, or None if it is at several — see commission_rate_for."""
    sites = list(show.sites.all())
    return sites[0] if len(sites) == 1 else None


def custody_for(show, venue=None):
    """When the gallery becomes responsible for the work, and when it stops.

    Two different dates at the end. `pickup_by` is when the artist is asked to collect;
    `until` is when the gallery's responsibility ends whether they did or not. Without the
    second, "until it is collected" has no end and an artist who never comes back leaves the
    gallery liable for their work indefinitely.
    """
    venue = venue if venue is not None else venue_of(show)
    windows = list(show.schedule_windows.all())
    dropoffs = [w.date for w in windows if w.kind != 'pickup']
    pickups = [w.date for w in windows if w.kind == 'pickup']
    grace = venue.custody_grace_days if venue else DEFAULT_CUSTODY_GRACE_DAYS
    pickup_by = max(pickups) if pickups else show.end
    return {
        'from': min(dropoffs) if dropoffs else None,
        'pickup_by': pickup_by,
        'grace_days': grace,
        'until': pickup_by + dt.timedelta(days=grace) if pickup_by else None,
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
    venue = venue_of(show)
    custody = custody_for(show, venue)

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
            'email': venue.email if venue else '',
            'website': (venue.website or '') if venue else '',
            # The storage path, not the bytes. A snapshot holds what was agreed, and the
            # venue's mark is not a term — if the logo file is later replaced the old
            # agreement can render with the new one, and if it is gone the PDF simply has
            # no logo rather than failing.
            'logo': _logo_name(venue),
        },
        'show': {
            'name': show.name,
            'slug': show.slug,
            'start': show.start.isoformat() if show.start else None,
            'end': show.end.isoformat() if show.end else None,
            'curators': [str(c) for c in show.ordered_curators],
        },
        'custody': {
            'from': custody['from'].isoformat() if custody['from'] else None,
            'pickup_by': custody['pickup_by'].isoformat() if custody['pickup_by'] else None,
            'grace_days': custody['grace_days'],
            'to': custody['until'].isoformat() if custody['until'] else None,
        },
        'commission_rate': str(rate),
        'artworks': rows,
        'total_agreed_value': sum(r['agreed_value'] or 0 for r in rows),
        'terms': terms_text(rate),
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

    Only while the gallery still has the work, though. A piece may be in several shows —
    most of this collection is — and `Artwork.agreed_value` is one field shared across
    all of them. Setting an agreed value while signing for this autumn's show therefore
    changes the number last year's agreement was signed against, and without this guard the
    artist would be asked to re-sign an agreement for a show that ended months ago, about
    work the gallery gave back. Once custody has ended there is nothing left to agree.
    """
    if not consignment.is_signed:
        return False
    ends = custody_for(consignment.show).get('until')
    if ends and ends < dt.date.today():
        return False
    try:
        return current_fingerprint(consignment.show, consignment.artist) != consignment.fingerprint
    except NoCommissionRate:
        return False


# A stated value this far above the asking price is worth a human look before the work is
# accepted. Not a limit and never shown to the artist — it only marks a row on the staff
# dashboard, where somebody can ask about it before the piece is in the building.
OUTLIER_RATIO = 3
OUTLIER_ABSOLUTE = 5000


def is_outlier(row):
    """Whether this piece's agreed value deserves a second look.

    The gallery is liable for the full stated value, and the artist sets it. There is no
    cap — a cap would refuse honest work priced unusually — but a figure several times the
    asking price, or large in absolute terms, is worth querying while refusing the piece is
    still possible. Once the work is in the door and the agreement signed, the number binds.
    """
    value = row.get('agreed_value')
    if value is None:
        return False
    price = row.get('price')
    if price:
        return value > price * OUTLIER_RATIO or value >= OUTLIER_ABSOLUTE
    return value >= OUTLIER_ABSOLUTE


def terms_text(rate=None):
    """The boilerplate, versioned.

    Takes the rate because "the gallery keeps the commission shown above" is nonsense when
    the commission is nothing. A show on those terms should say so plainly rather than
    print 0% and leave the artist working out what it means.

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
            'the artist collects it — whether or not it sells. The artist is responsible '
            'for getting it here, and a buyer for taking it away once it is theirs.',
            'That responsibility ends on the date shown above, which is a set number of days '
            'after the last pickup time. Work still here after that date is held at the '
            'artist’s risk: the gallery will look after it, but is no longer responsible '
            'for loss or damage, and does not pay its agreed value.',
            'If a piece is lost, stolen or damaged beyond repair while in the gallery’s '
            'care, the gallery pays the artist that piece’s agreed value in full — not the '
            'agreed value less commission.',
            'The agreed value is exactly that — agreed. The artist proposes it, and the '
            'gallery may query it or decline to take a piece before it is dropped off. '
            'Once the work has been accepted the figure above is what applies.',
            'The gallery will not alter, copy or lend the work, and will take reasonable '
            'care of it.',
        ]),
        ('If it sells', [
            'The gallery takes no commission on this show. The artist receives the full '
            'sale price.'
            if rate is not None and rate == 0 else
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
            'Work that is not collected is not abandoned — the gallery will hold it and get '
            'in touch — but from the date above it is at the artist’s risk.',
        ]),
    ]
