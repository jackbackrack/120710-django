from zoneinfo import available_timezones

from django.core.exceptions import ValidationError
from django.db import models
from django_countries.fields import CountryField
from django.urls import reverse
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit, Transpose

from gallery import timeranges
from gallery.models.slugs import build_unique_slug


def validate_timezone(value):
    """An IANA zone name, e.g. America/Los_Angeles."""
    if value and value not in available_timezones():
        raise ValidationError(
            f'{value!r} is not an IANA time zone name (e.g. America/Los_Angeles).')


# US states that lie wholly in one zone, so the venue's address answers the question and
# nobody has to. Deliberately incomplete: the split states — FL, IN, KY, TN, TX, KS, NE, ND,
# SD, ID, OR, NV, MI, AK — are left out rather than guessed at, because a venue in Pensacola
# is Central while the rest of Florida is Eastern, and being quietly an hour wrong in a
# published calendar feed is worse than asking. Those fall back to the dropdown.
_STATE_TIMEZONES = {
    'America/Los_Angeles': ['CA', 'WA'],
    'America/Denver': ['CO', 'MT', 'NM', 'UT', 'WY'],
    'America/Phoenix': ['AZ'],
    'America/Chicago': ['AL', 'AR', 'IA', 'IL', 'LA', 'MN', 'MS', 'MO', 'OK', 'WI'],
    'America/New_York': ['CT', 'DC', 'DE', 'GA', 'MA', 'MD', 'ME', 'NC', 'NH', 'NJ', 'NY',
                         'OH', 'PA', 'RI', 'SC', 'VA', 'VT', 'WV'],
    'Pacific/Honolulu': ['HI'],
}
_STATE_TO_TIMEZONE = {
    state: zone for zone, states in _STATE_TIMEZONES.items() for state in states
}

# Full state names too, since the field is free text and people type either.
_STATE_NAMES = {
    'california': 'CA', 'washington': 'WA', 'colorado': 'CO', 'montana': 'MT',
    'new mexico': 'NM', 'utah': 'UT', 'wyoming': 'WY', 'arizona': 'AZ',
    'alabama': 'AL', 'arkansas': 'AR', 'iowa': 'IA', 'illinois': 'IL',
    'louisiana': 'LA', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO',
    'oklahoma': 'OK', 'wisconsin': 'WI', 'connecticut': 'CT', 'delaware': 'DE',
    'georgia': 'GA', 'massachusetts': 'MA', 'maryland': 'MD', 'maine': 'ME',
    'north carolina': 'NC', 'new hampshire': 'NH', 'new jersey': 'NJ', 'new york': 'NY',
    'ohio': 'OH', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'virginia': 'VA', 'vermont': 'VT', 'west virginia': 'WV', 'hawaii': 'HI',
}


def timezone_from_address(state, country):
    """The venue's zone derived from its address, or '' when the address cannot settle it.

    Only US states, and only the ones that lie in a single zone. The ZIP code is a worse key
    than the state for this: ZIPs cross county and occasionally state lines, PO-box ZIPs have
    no geography at all, and the Census data already cached for the submission catchment
    carries centroids but no zones. Latitude and longitude *would* settle it exactly, but
    only with a timezone-boundary dataset — tens of megabytes of polygons to answer a
    question a dropdown answers for the handful of venues that need it.
    """
    if str(country or '') != 'US':
        return ''
    text = (state or '').strip()
    code = text.upper() if len(text) == 2 else _STATE_NAMES.get(text.lower(), '')
    return _STATE_TO_TIMEZONE.get(code, '')


class Site(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
    ]
    PUBLIC_STATUSES = {STATUS_PUBLISHED}

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    street = models.CharField(max_length=255, blank=True, verbose_name='Street address')
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True, verbose_name='State / Province / Region')
    postal_code = models.CharField(max_length=20, blank=True)
    # ISO 3166-1 alpha-2, matching Artist.country, so a national show can compare
    # the two directly. This was free text holding "USA", which meant the
    # comparison needed a table of spellings to guess at.
    country = CountryField(default='US')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    instagram = models.CharField(max_length=100, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    description = models.TextField(blank=True)
    # The four public info pages (/about/, /visit/, /contact/, /links/) used to be
    # hard-coded templates naming one gallery. They read from here now, falling back to
    # the deployment's default site — so a second gallery gets its own without a deploy.
    hours = models.CharField(
        max_length=255, blank=True, default='', verbose_name='Opening hours',
        help_text='Shown on the Visit and Contact pages, e.g. '
                  '"Sun 1-4p or by Appt MWF 12-6p".')
    about = models.TextField(
        blank=True, default='',
        help_text='The Info page: mission, story, people. Accepts formatting, headings, '
                  'tables and images.')
    visit_notes = models.TextField(
        blank=True, default='', verbose_name='Getting here',
        help_text='Parking, transit and directions, shown on the Visit page below the '
                  'address.')
    visit_image = models.ImageField(
        upload_to='site_visit', blank=True, null=True, verbose_name='Visit photo',
        help_text='A street view or storefront photo for the Visit page.')
    image = models.ImageField(upload_to='site_images', blank=True, null=True)
    card_sm = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=200)], format='JPEG', options={'quality': 80})
    card_md = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=600)], format='JPEG', options={'quality': 80})
    detail_lg = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=1200)], format='JPEG', options={'quality': 85})
    icon = models.ImageField(upload_to='site_icons', blank=True, null=True, help_text='Small logo or icon for the site (shown in nav and cards).')
    icon_sm = ImageSpecField(source='icon', processors=[Transpose(), ResizeToFit(width=32, height=32)], format='PNG', options={'quality': 90})
    # For email, where the logo is a masthead rather than a favicon. 300px so it stays sharp on
    # a retina screen at the ~150px it is displayed at. ImageSpecFields are derived files, not
    # columns, so adding one needs no migration.
    icon_md = ImageSpecField(source='icon', processors=[Transpose(), ResizeToFit(width=300)], format='PNG', options={'quality': 90})
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # ── Booking a visit ──────────────────────────────────────────────────────
    # Off until a venue has entered structured opening hours, because slots are computed from
    # them and a venue with none would offer nothing and look broken.
    visits_enabled = models.BooleanField(
        default=False, verbose_name='Let visitors book a time',
        help_text='Needs opening hours entered below. Visitors pick a slot and you get a '
                  'calendar invitation by email.')
    visit_slot_minutes = models.PositiveSmallIntegerField(
        default=30, verbose_name='Slot length (minutes)')
    # Zero means no limit. Slots are deliberately shared — several visitors at the same time is
    # fewer appointments to keep, not a clash — but a school group of twelve is worth a ceiling.
    visit_capacity = models.PositiveSmallIntegerField(
        default=0, verbose_name='People per slot',
        help_text='0 for no limit. Visits share a slot on purpose; this is only a ceiling.')
    visit_lead_hours = models.PositiveSmallIntegerField(
        default=2, verbose_name='Notice required (hours)',
        help_text='Slots sooner than this are not offered.')
    visit_horizon_days = models.PositiveSmallIntegerField(
        default=30, verbose_name='Book up to (days ahead)')
    # Which postal codes count as "in this venue's area", for shows whose scope is local.
    # Stored as a list rather than as a rule (a radius, a set of counties) so the boundary
    # stays editable: if one postal code is wrong you fix that postal code, without a
    # deploy and without anyone needing to understand the rule that generated it.
    # `manage.py set_site_catchment` writes both fields; nobody maintains them by hand.
    # Empty means no checking at all, which is what every site does until opted in.
    submission_zipcodes = models.TextField(
        blank=True, default='',
        verbose_name='Local postal codes',
        help_text='Postal codes counting as local to this venue, separated by spaces, '
                  'commas or newlines. Leave blank to disable area checking. Generated '
                  'by `manage.py set_site_catchment`.')
    submission_area_label = models.CharField(
        max_length=120, blank=True, default='',
        verbose_name='Local area name',
        help_text='How the area is described to a curator, e.g. "Bay Area (9 counties)".')
    # The venue's wall-clock zone. Event.date/start/end are naive fields — a 6pm opening is
    # stored as 18:00 with no zone — and TIME_ZONE is UTC, so nothing in the database says
    # where 6pm *is*. That is harmless while every page renders it verbatim to local
    # readers, and wrong the moment a calendar feed publishes an instant: without this,
    # 18:00 published as UTC shows up as 11am in Berkeley. Validated rather than given
    # `choices`, because 600 zone names in the field definition would be copied into every
    # migration that touches it; the form supplies the dropdown instead.
    timezone = models.CharField(
        max_length=64, blank=True, default='', validators=[validate_timezone],
        verbose_name='Time zone',
        help_text='IANA name for the venue\u2019s local time, e.g. America/Los_Angeles. '
                  'Used to publish event times correctly in the calendar feed. Filled in '
                  'from the state when that settles it; set it by hand otherwise.')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    @property
    def formatted_address(self):
        city_line = ', '.join(filter(None, [self.city, self.state]))
        if self.postal_code:
            city_line = f'{city_line} {self.postal_code}' if city_line else self.postal_code
        # .name, not the field: a CountryField stringifies to its two-letter code,
        # and an address ending in "US" reads like a bug.
        country = self.country.name if self.country else ''
        lines = [l for l in [self.street, city_line, country] if l]
        return '\n'.join(lines)

    # ── Opening hours ────────────────────────────────────────────────────────

    def open_periods_on(self, day, include_appointment=True):
        """The blocks this venue is open on one date, closures already removed.

        The primitive everything else is built on — the display text, the schema.org output and,
        eventually, the visit scheduler's list of candidate slots. Returns [] for a closed day,
        which is the same answer as "no hours recorded"; a venue that has entered nothing is
        indistinguishable from one that is never open, and neither should be offered a slot.
        """
        if any(closure.covers(day) for closure in self.closures.all()):
            return []
        blocks = [b for b in self.opening_hours.all() if b.weekday == day.weekday()]
        if not include_appointment:
            blocks = [b for b in blocks if not b.by_appointment]
        return sorted(blocks, key=lambda b: b.start)

    def is_open_on(self, day):
        """Open to the public, walk-in — appointment-only does not count as open."""
        return bool(self.open_periods_on(day, include_appointment=False))

    def closure_on(self, day):
        return next((c for c in self.closures.all() if c.covers(day)), None)

    @property
    def has_structured_hours(self):
        return self.opening_hours.exists()

    @property
    def hours_display(self):
        """Opening hours as a line a visitor can read, from the structured blocks.

        Falls back to the free-text `hours` field when nothing structured has been entered, so a
        venue that has not been touched reads exactly as it did before. Structured wins once it
        exists, because two sources that can disagree will.
        """
        blocks = list(self.opening_hours.all())
        if not blocks:
            return self.hours

        # Grouped by identical hours so a week of the same times is one line rather than seven.
        grouped = {}
        for block in blocks:
            grouped.setdefault((block.start, block.end, block.by_appointment), []).append(
                block.weekday)

        # Drop-in hours before appointment-only ones: the first is what anybody can act on
        # today, and leading with "by appointment" reads like the gallery is shut.
        parts = []
        for (start, end, by_appointment), days in sorted(
                grouped.items(), key=lambda item: (item[0][2], min(item[1]), item[0][0])):
            line = f'{timeranges.weekday_ranges(days)} {timeranges.time_range(start, end)}'
            parts.append(f'{line} by appointment' if by_appointment else line)
        return ' · '.join(parts)

    @property
    def schema_opening_hours(self):
        """schema.org `openingHours`, e.g. ["Su 13:00-16:00"].

        Drop-in hours only. Telling a search engine a venue is open when the door is locked
        unless you rang ahead is worse than telling it nothing.
        """
        out = []
        for block in self.opening_hours.all():
            if block.by_appointment:
                continue
            day = timeranges.WEEKDAY_SCHEMA[block.weekday]
            out.append(f'{day} {block.start.strftime("%H:%M")}-{block.end.strftime("%H:%M")}')
        return out

    @property
    def maps_url(self):
        """A link that opens this address in a map, or '' if there is no address.

        Coordinates when they exist, because a search by street address lands on the wrong side
        of the block often enough to matter for somebody arriving at an opening.
        """
        if self.latitude is not None and self.longitude is not None:
            return ('https://www.google.com/maps/search/?api=1&query='
                    f'{self.latitude},{self.longitude}')
        parts = [p for p in (self.street, self.city, self.state) if p]
        if not parts:
            return ''
        from urllib.parse import quote_plus
        return f'https://www.google.com/maps/search/?api=1&query={quote_plus(", ".join(parts))}'

    def save(self, *args, **kwargs):
        self.slug = build_unique_slug(self, self.name)
        # Derived only when unset, so an explicit choice is never overwritten by an address
        # edit. Blank stays blank when the address cannot settle it — the feed falls back to
        # the gallery's own zone and the form asks.
        if not self.timezone:
            self.timezone = timezone_from_address(self.state, self.country)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('gallery:site_detail', kwargs={'slug': self.slug})


class OpeningHours(models.Model):
    """One recurring block of time a venue is open, on one weekday.

    Replaces reading opening hours out of a sentence. `Site.hours` is prose — "Sun 1-4p or by
    Appt MWF 12-6p" — which is fine for a human and useless to anything that has to decide
    whether a Tuesday at 3pm is bookable. Parsing it was never an option: the first venue to
    write "closed in August" breaks any regex that ever worked.

    `by_appointment` matters because those two kinds of time are genuinely different. A drop-in
    hour is what schema.org means by `openingHours`; an appointment hour is not open to the
    public but *is* a slot somebody can ask for, so the visit scheduler wants both and search
    engines should only see the first.
    """

    site = models.ForeignKey('gallery.Site', on_delete=models.CASCADE,
                             related_name='opening_hours')
    # 0 is Monday, matching datetime.date.weekday(), so "is the gallery open on this date" is an
    # integer comparison rather than a name lookup.
    weekday = models.IntegerField(
        choices=[(i, name) for i, name in enumerate(timeranges.WEEKDAYS)])
    start = models.TimeField()
    end = models.TimeField()
    by_appointment = models.BooleanField(
        default=False, verbose_name='By appointment only',
        help_text='Not open to the public at this time, but visits can be arranged. Left out of '
                  'what search engines are told, and offered by the visit scheduler.')

    class Meta:
        ordering = ['weekday', 'start']
        verbose_name_plural = 'opening hours'
        constraints = [
            models.UniqueConstraint(fields=['site', 'weekday', 'start', 'end'],
                                    name='unique_opening_block'),
        ]

    def __str__(self):
        suffix = ' by appointment' if self.by_appointment else ''
        return (f'{self.site.name} {timeranges.WEEKDAY_ABBR[self.weekday]} '
                f'{self.time_range}{suffix}')

    @property
    def time_range(self):
        return timeranges.time_range(self.start, self.end)


class SiteClosure(models.Model):
    """A date range a venue is shut regardless of its usual hours.

    Without this the scheduler cheerfully offers Christmas Day, and the only way to prevent it is
    to delete the opening hours and remember to put them back.
    """

    site = models.ForeignKey('gallery.Site', on_delete=models.CASCADE,
                             related_name='closures')
    start_date = models.DateField()
    # Inclusive, because "closed 24–26 December" is how anybody says it, and an exclusive end
    # date is the kind of off-by-one nobody notices until the door is locked.
    end_date = models.DateField(help_text='Inclusive — the last day closed.')
    note = models.CharField(max_length=255, blank=True, default='',
                            help_text='Shown to visitors, e.g. "Between shows".')

    class Meta:
        ordering = ['start_date']
        verbose_name_plural = 'closures'

    def __str__(self):
        span = (f'{self.start_date}' if self.start_date == self.end_date
                else f'{self.start_date}–{self.end_date}')
        return f'{self.site.name} closed {span}'

    def covers(self, day):
        return self.start_date <= day <= self.end_date
