"""Is a submission from inside the show's allowed area?

This flags submissions for the curator. It never blocks anyone, and it never rejects
anything: an artist can always submit, and the curator decides. That is deliberate —
postal codes are self-reported, a studio address is not a home address, people move
mid-call, and PO-box ZIP codes have no ZCTA at all. A hard block would turn every one of
those into an email.

Three outcomes, not two. "We could not tell" is a different problem from "they are
outside the area" and a curator acts on them differently, so they are separate states.
"""
from gallery.models import Show

IN_AREA = 'in'
OUT_OF_AREA = 'out'
UNKNOWN = 'unknown'

# Both Site.country and Artist.country are CountryFields now, so the national check is a
# direct comparison. These aliases remain only as a safety net for anything that reaches
# here as free text (a fixture, an import); anything unrecognised yields UNKNOWN rather
# than a guess, which is the honest answer.
_COUNTRY_ALIASES = {
    'us': 'US', 'usa': 'US', 'u.s.': 'US', 'u.s.a.': 'US',
    'united states': 'US', 'united states of america': 'US',
}


def normalize_country(value):
    """A free-text country name to an ISO 3166 alpha-2 code, or None if unrecognised."""
    text = (str(value or '')).strip()
    if not text:
        return None
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _COUNTRY_ALIASES.get(text.lower())


def parse_zipcodes(raw):
    """The stored catchment blob to a set of normalized postal codes.

    Anything after a `#` on a line is dropped. This is a text area a person edits by hand
    — someone will want to write down why three codes on the far side of a bay were
    deleted — and a note silently becoming a postal code nobody matches is a bad trade.
    """
    codes = set()
    for line in (raw or '').splitlines():
        line = line.split('#', 1)[0]
        codes |= {part.strip().upper()
                  for part in line.replace(',', ' ').split() if part.strip()}
    return codes


def normalize_zipcode(value):
    """Compare on the leading five digits for US ZIPs, so 94710-1234 matches 94710."""
    text = (value or '').strip().upper()
    head = text.split('-', 1)[0].strip()
    return head or ''


def site_catchment(show):
    """(postal codes, label) for the show's venues, unioned. Empty set = not configured."""
    codes, labels = set(), []
    for site in show.sites.all():
        site_codes = parse_zipcodes(site.submission_zipcodes)
        if site_codes:
            codes |= site_codes
            if site.submission_area_label:
                labels.append(site.submission_area_label)
    return codes, ' / '.join(dict.fromkeys(labels))


class AreaCheck:
    """The show's scope, catchment and venue countries, loaded once.

    The venues have to be read to answer anything here, and the caller is normally a
    curation page looping over every submission. Built per request rather than per
    submission because the first version was the latter: a 60-submission page went from
    13 queries to 193, since `show.sites.all()` is a fresh query every time it is called.
    """

    __slots__ = ('scope', 'codes', 'label', 'countries')

    def __init__(self, show):
        self.scope = getattr(show, 'submission_scope', Show.SCOPE_ANYWHERE)
        # One evaluation of the M2M, reused for both the catchment and the countries.
        sites = list(show.sites.all())
        codes, labels = set(), []
        for site in sites:
            site_codes = parse_zipcodes(site.submission_zipcodes)
            if site_codes:
                codes |= site_codes
                if site.submission_area_label:
                    labels.append(site.submission_area_label)
        self.codes = codes
        self.label = ' / '.join(dict.fromkeys(labels))
        self.countries = {normalize_country(s.country) for s in sites}
        self.countries.discard(None)

    def status(self, artist):
        """Where this artist sits relative to the show's scope.

        Returns None when there is nothing to say — the show accepts work from anywhere,
        or it is local but its venue has no catchment configured yet. Returning None
        rather than IN_AREA matters: the caller shows no badge at all, instead of quietly
        asserting that an unconfigured site accepted someone.
        """
        if artist is None or self.scope == Show.SCOPE_ANYWHERE:
            return None

        artist_country = normalize_country(getattr(artist, 'country', None))

        if self.scope == Show.SCOPE_NATIONAL:
            if not self.countries:
                return None          # nothing to compare against
            if artist_country is None:
                return UNKNOWN
            return IN_AREA if artist_country in self.countries else OUT_OF_AREA

        # Local.
        if not self.codes:
            return None              # venue has not been given a catchment yet
        zipcode = normalize_zipcode(getattr(artist, 'zipcode', ''))
        if not zipcode:
            return UNKNOWN
        # A postal code outside the venue's country can never be in a US-derived
        # catchment, and saying "outside the area" is more useful than "we could not
        # tell".
        if artist_country and artist_country not in self.countries:
            return OUT_OF_AREA
        return IN_AREA if zipcode in self.codes else OUT_OF_AREA

    def describe(self, artist, status, blind=False):
        """Short label for the curation UI, or '' when there is nothing to show.

        `blind` suppresses the detail during blind review. A location is identifying in
        the same way a name is — if you have decided not to know who made it, you have
        decided not to know where they are. The badge itself still appears, because
        eligibility is a policy question rather than an aesthetic one.
        """
        if status in (None, IN_AREA):
            return ''
        if status == UNKNOWN:
            return 'Location not given'
        if blind:
            return 'Outside area'
        where = normalize_zipcode(getattr(artist, 'zipcode', ''))
        # The country only when it is not the venue's own. On a Berkeley show, appending
        # "United States of America" to every out-of-area badge says nothing a curator
        # did not already know, and it crowds out the postal code, which says everything.
        artist_country = normalize_country(getattr(artist, 'country', None))
        country_name = ''
        if artist_country and artist_country not in self.countries:
            country = getattr(artist, 'country', None)
            country_name = getattr(country, 'name', '') or ''
        detail = ' · '.join(part for part in (where, country_name) if part)
        # "Outside Bay Area (9 counties)", not "Outside area (Bay Area (9 counties))" —
        # the label is already a noun phrase, so wrapping it in parentheses nests them.
        head = f'Outside {self.label}' if self.label else 'Outside area'
        return head + (f' · {detail}' if detail else '')


# One-artist wrappers. Convenient for a single check (a warning at submission time, a
# test); wasteful in a loop, which is what AreaCheck is for.

def check_artist(show, artist):
    return AreaCheck(show).status(artist)


def describe(show, artist, status, blind=False):
    return AreaCheck(show).describe(artist, status, blind=blind)
