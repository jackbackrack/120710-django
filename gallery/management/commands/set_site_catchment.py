"""Write a venue's local postal codes, from counties, a radius, or a file.

The catchment is stored on the Site as a plain list of postal codes rather than as the
rule that produced it. That is deliberate: no real "local artists" boundary is a circle or
a county line either. Storing the output means a curator can delete the three codes on the
far side of a bay, or paste in the two the rule missed, without anyone re-deriving
anything. Re-running this command overwrites those edits, which is why it prints what it
is about to change and takes --dry-run.

Prefer --counties. A radius from Berkeley either drops Gilroy (at 60 miles) or swallows
Sacramento (at 75); the nine-county Bay Area is a boundary people already agree on.

Both data sources are US Census files, downloaded once and cached under .zcta_cache/.
They only cover the United States, so a venue elsewhere needs --from-file.

    manage.py set_site_catchment 120710 --state CA --label "Bay Area (9 counties)" \
        --counties "Alameda, Contra Costa, Marin, Napa, San Francisco, San Mateo, \
                    Santa Clara, Solano, Sonoma"
    manage.py set_site_catchment 120710 --radius 60 --center 37.8716,-122.2727 --dry-run
    manage.py set_site_catchment 120710 --from-file bay-area-zips.txt --label "Bay Area"
    manage.py set_site_catchment --list
    manage.py set_site_catchment 120710 --clear
"""
import io
import math
import os
import zipfile
from urllib.request import urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from gallery.models import Site
from gallery.submission_area import parse_zipcodes

GAZETTEER_YEAR = '2025'
GAZETTEER_URL = (
    'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/'
    f'{GAZETTEER_YEAR}_Gazetteer/{GAZETTEER_YEAR}_Gaz_zcta_national.zip'
)
# Which ZCTAs fall in which county. A circle drawn from a venue is a poor model of a
# real catchment — 60 miles from Berkeley drops Gilroy and 75 swallows Sacramento —
# so counties are usually the honest boundary.
CROSSWALK_URL = ('https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/'
                 'tab20_zcta520_county20_natl.txt')
CACHE_DIR = os.path.join(settings.BASE_DIR, '.zcta_cache')
CACHE_PATH = os.path.join(CACHE_DIR, f'{GAZETTEER_YEAR}_Gaz_zcta_national.txt')
CROSSWALK_PATH = os.path.join(CACHE_DIR, 'zcta_county_crosswalk.txt')

STATE_FIPS = {
    'AL': '01', 'AK': '02', 'AZ': '04', 'AR': '05', 'CA': '06', 'CO': '08', 'CT': '09',
    'DE': '10', 'DC': '11', 'FL': '12', 'GA': '13', 'HI': '15', 'ID': '16', 'IL': '17',
    'IN': '18', 'IA': '19', 'KS': '20', 'KY': '21', 'LA': '22', 'ME': '23', 'MD': '24',
    'MA': '25', 'MI': '26', 'MN': '27', 'MS': '28', 'MO': '29', 'MT': '30', 'NE': '31',
    'NV': '32', 'NH': '33', 'NJ': '34', 'NM': '35', 'NY': '36', 'NC': '37', 'ND': '38',
    'OH': '39', 'OK': '40', 'OR': '41', 'PA': '42', 'RI': '44', 'SC': '45', 'SD': '46',
    'TN': '47', 'TX': '48', 'UT': '49', 'VT': '50', 'VA': '51', 'WA': '53', 'WV': '54',
    'WI': '55', 'WY': '56', 'PR': '72',
}

EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance. Good to a fraction of a mile at these scales, which is far
    finer than the question being asked ("is this artist local?")."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _cached(url, path, refresh, stdout, unzip=False):
    """Fetch `url` to `path` once. Census files are static per vintage, so a cache hit is
    the normal case and the network is only touched on a fresh checkout."""
    if not refresh and os.path.exists(path):
        return path
    if stdout:
        stdout.write(f'Downloading {url} ...')
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with urlopen(url, timeout=300) as response:
            payload = response.read()
    except Exception as exc:                       # network, DNS, 404, timeout
        raise CommandError(
            f'Could not download {url}: {exc}\n'
            f'Download it by hand, put it at {path}, and re-run; or use --from-file.')
    if unzip:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            name = next(n for n in archive.namelist() if n.endswith('.txt'))
            payload = archive.read(name)
    with open(path, 'wb') as out:
        out.write(payload)
    return path


def load_county_zctas(refresh=False, stdout=None):
    """{county FIPS: {postal codes}} — a ZCTA straddling a county line appears in both."""
    _cached(CROSSWALK_URL, CROSSWALK_PATH, refresh, stdout)
    by_county = {}
    with open(CROSSWALK_PATH, encoding='utf-8-sig') as handle:
        header = [c.strip() for c in next(handle).split('|')]
        try:
            zcta_i = header.index('GEOID_ZCTA5_20')
            fips_i = header.index('GEOID_COUNTY_20')
            name_i = header.index('NAMELSAD_COUNTY_20')
        except ValueError:
            raise CommandError(f'Unexpected crosswalk columns: {header}')
        names = {}
        for line in handle:
            cols = line.split('|')
            if len(cols) <= name_i:
                continue
            zcta, fips = cols[zcta_i].strip(), cols[fips_i].strip()
            # Rows exist for counties with no ZCTA part; they carry a blank ZCTA.
            if not zcta or not fips:
                continue
            by_county.setdefault(fips, set()).add(zcta)
            names[fips] = cols[name_i].strip()
    if not by_county:
        raise CommandError(f'No rows parsed from {CROSSWALK_PATH}.')
    return by_county, names


def load_centroids(refresh=False, stdout=None):
    """{'94710': (lat, lon)} for every US ZCTA, downloading the gazetteer if needed."""
    _cached(GAZETTEER_URL, CACHE_PATH, refresh, stdout, unzip=True)

    centroids = {}
    # Latin-1, with columns padded out with spaces. The delimiter changed from tab to
    # pipe between gazetteer years, so take it from the header rather than assuming.
    with open(CACHE_PATH, encoding='latin-1') as handle:
        header_line = next(handle)
        delimiter = '|' if '|' in header_line else '\t'
        header = [c.strip() for c in header_line.split(delimiter)]
        try:
            geoid_i = header.index('GEOID')
            lat_i = header.index('INTPTLAT')
            lon_i = header.index('INTPTLONG')
        except ValueError:
            raise CommandError(
                f'Unexpected gazetteer columns in {CACHE_PATH}: {header}. '
                f'The Census file format may have changed.')
        for line in handle:
            cols = line.split(delimiter)
            if len(cols) <= lon_i:
                continue
            try:
                centroids[cols[geoid_i].strip()] = (
                    float(cols[lat_i]), float(cols[lon_i]))
            except ValueError:
                continue
    if not centroids:
        raise CommandError(f'No ZCTA rows parsed from {CACHE_PATH}.')
    return centroids


class Command(BaseCommand):
    help = "Set a venue's local postal codes, from counties, a radius, or a file."

    def add_arguments(self, parser):
        parser.add_argument('slug', nargs='?', help='Site slug.')
        parser.add_argument('--list', action='store_true',
                            help='Show every site\'s current catchment and exit.')
        parser.add_argument('--radius', type=float,
                            help='Miles from the centre point.')
        parser.add_argument('--center', '--centre', dest='center',
                            help='"lat,lon". Defaults to the site\'s own coordinates.')
        parser.add_argument('--counties',
                            help='Comma-separated county names or FIPS codes, e.g. '
                                 '"Alameda, Contra Costa, Marin". Needs --state unless '
                                 'you give FIPS codes.')
        parser.add_argument('--state',
                            help='Two-letter state code for --counties, e.g. CA.')
        parser.add_argument('--from-file',
                            help='Read postal codes from a file instead of computing '
                                 'them. Separated by spaces, commas or newlines.')
        parser.add_argument('--label',
                            help='How the area is described to a curator, '
                                 'e.g. "Bay Area (60 miles)".')
        parser.add_argument('--clear', action='store_true',
                            help='Remove the catchment, disabling area checking.')
        parser.add_argument('--refresh', action='store_true',
                            help='Re-download the Census gazetteer.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would change without saving.')

    def handle(self, *args, **options):
        if options['list']:
            return self._list()

        slug = options['slug']
        if not slug:
            raise CommandError('Give a site slug, or --list.')
        try:
            site = Site.objects.get(slug=slug)
        except Site.DoesNotExist:
            known = ', '.join(Site.objects.values_list('slug', flat=True)) or 'none'
            raise CommandError(f'No site with slug {slug!r}. Known sites: {known}')

        if options['clear']:
            codes, label = set(), ''
        elif options['from_file']:
            codes, label = self._from_file(options['from_file']), options['label']
        elif options['counties']:
            codes, label = self._from_counties(options)
        elif options['radius']:
            codes, label = self._from_radius(site, options)
        else:
            raise CommandError(
                'Give one of --counties, --radius, --from-file or --clear.')

        self._apply(site, codes, label, options)

    # --- Sources ---

    def _from_file(self, path):
        try:
            with open(path, encoding='utf-8') as handle:
                codes = parse_zipcodes(handle.read())
        except OSError as exc:
            raise CommandError(f'Could not read {path}: {exc}')
        if not codes:
            raise CommandError(f'{path} contained no postal codes.')
        return codes

    def _from_counties(self, options):
        wanted = [c.strip() for c in options['counties'].split(',') if c.strip()]
        by_county, names = load_county_zctas(
            refresh=options['refresh'], stdout=self.stdout)

        state_prefix = None
        if options['state']:
            state_prefix = STATE_FIPS.get(options['state'].strip().upper())
            if not state_prefix:
                raise CommandError(f'Unknown state code {options["state"]!r}.')

        codes, resolved = set(), []
        for entry in wanted:
            if entry.isdigit():
                fips = entry.zfill(5)
                if fips not in by_county:
                    raise CommandError(f'No county with FIPS {fips}.')
            else:
                # Names repeat across states ("Orange County" is in seven), so a name
                # without --state is ambiguous and refused rather than guessed at.
                target = entry.lower().removesuffix(' county').strip()
                matches = [f for f, n in names.items()
                           if n.lower().removesuffix(' county').strip() == target
                           and (state_prefix is None or f.startswith(state_prefix))]
                if not matches:
                    where = f' in {options["state"]}' if state_prefix else ''
                    raise CommandError(f'No county named {entry!r}{where}.')
                if len(matches) > 1:
                    found = ', '.join(sorted(matches))
                    raise CommandError(
                        f'{entry!r} matches several counties ({found}). '
                        f'Pass --state, or give the FIPS code.')
                fips = matches[0]
            codes |= by_county[fips]
            resolved.append(names.get(fips, fips))

        self.stdout.write(
            f'{len(codes)} postal codes across {len(resolved)} counties: '
            + ', '.join(resolved))
        return codes, options['label'] or ', '.join(resolved)

    def _from_radius(self, site, options):
        if options['center']:
            try:
                lat_text, lon_text = options['center'].split(',')
                lat, lon = float(lat_text), float(lon_text)
            except ValueError:
                raise CommandError('--center must look like "37.8716,-122.2727".')
        elif site.latitude is not None and site.longitude is not None:
            lat, lon = float(site.latitude), float(site.longitude)
            self.stdout.write(f'Centring on {site.name}: {lat}, {lon}')
        else:
            raise CommandError(
                f'{site.name} has no coordinates, so there is nothing to measure from. '
                f'Set them on the site, or pass --center "lat,lon".')

        radius = options['radius']
        centroids = load_centroids(refresh=options['refresh'], stdout=self.stdout)
        within = {code: haversine_miles(lat, lon, clat, clon)
                  for code, (clat, clon) in centroids.items()
                  if haversine_miles(lat, lon, clat, clon) <= radius}
        if not within:
            raise CommandError(
                f'No ZCTA centroid within {radius} miles of {lat}, {lon}. '
                f'Check the coordinates — latitude first.')

        farthest = max(within.items(), key=lambda kv: kv[1])
        self.stdout.write(
            f'{len(within)} postal codes within {radius} miles '
            f'(farthest: {farthest[0]} at {farthest[1]:.1f} mi)')
        label = options['label'] or f'within {radius:g} miles of {site.name}'
        return set(within), label

    # --- Output ---

    def _list(self):
        for site in Site.objects.all():
            codes = parse_zipcodes(site.submission_zipcodes)
            if codes:
                label = site.submission_area_label or '(unnamed area)'
                self.stdout.write(f'{site.slug}: {len(codes)} codes — {label}')
            else:
                self.stdout.write(f'{site.slug}: no catchment (area checking off)')

    def _apply(self, site, codes, label, options):
        before = parse_zipcodes(site.submission_zipcodes)
        added, removed = codes - before, before - codes
        if not added and not removed and (label or '') == site.submission_area_label:
            self.stdout.write(f'{site.name} is already up to date.')
            return

        self.stdout.write(f'{site.name}: {len(before)} -> {len(codes)} postal codes')
        if removed:
            # Named individually: these are the hand-edits a re-run would silently undo.
            shown = ' '.join(sorted(removed)[:20])
            more = f' (+{len(removed) - 20} more)' if len(removed) > 20 else ''
            self.stdout.write(self.style.WARNING(f'  removing {len(removed)}: {shown}{more}'))
        if added:
            self.stdout.write(f'  adding {len(added)}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('--dry-run: nothing saved.'))
            return

        # Sorted and wrapped, so the field stays diffable and readable in the admin.
        ordered = sorted(codes)
        site.submission_zipcodes = '\n'.join(
            ' '.join(ordered[i:i + 10]) for i in range(0, len(ordered), 10))
        if label is not None:
            site.submission_area_label = label or ''
        site.save(update_fields=['submission_zipcodes', 'submission_area_label'])
        self.stdout.write(self.style.SUCCESS(
            f'Saved. {site.name} area: {site.submission_area_label or "(unnamed)"}'
            if codes else f'Cleared. Area checking is off for {site.name}.'))
