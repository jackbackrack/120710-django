import datetime
import io
import os
import re
import shutil
import tempfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_staff_role
from gallery.models import Artist, Artwork, ArtworkSubmission, Event, Show, ShowArtworkNumber, ShowInvitation, Site
from gallery.models import Campaign, LinkTreeEntry, Subscriber, Subscription
from django.utils import timezone

from gallery import calendars
from gallery import campaigns
from gallery import submission_area


def _make_test_image_dir():
    """Return a temp directory containing artist_images/test.jpg (1x1 JPEG)."""
    from PIL import Image as PILImage
    tmp = tempfile.mkdtemp()
    img_dir = os.path.join(tmp, 'artist_images')
    os.makedirs(img_dir)
    PILImage.new('RGB', (2, 2), 'white').save(os.path.join(img_dir, 'test.jpg'), 'JPEG')
    return tmp



def _test_jpg(name='photo.jpg'):
    """A small JPEG with the variation of a photograph, for the places a photo is required.

    Deliberately not a solid colour any more. A flat square is what Google hands out for an
    account with no picture, and profile photos are now checked for exactly that — so a fixture
    that was a plain rectangle would be rejected here for the same reason it is rejected in
    production, which is the fixture being wrong rather than the check.
    """
    import io
    import random
    from PIL import Image as _P

    rng = random.Random(7)
    image = _P.new('RGB', (240, 240))
    pixels = image.load()
    for y in range(240):
        for x in range(240):
            # Smooth gradients plus grain: enough distinct shades to read as a photograph.
            pixels[x, y] = ((x + rng.randint(0, 40)) % 256,
                            (y * 2 + rng.randint(0, 40)) % 256,
                            ((x + y) + rng.randint(0, 40)) % 256)
    b = io.BytesIO(); image.save(b, 'JPEG')
    return SimpleUploadedFile(name, b.getvalue(), content_type='image/jpeg')


class MediaImageMixin:
    """Mixin that sets up a real MEDIA_ROOT with a tiny test image.

    Use in test classes that need artist.image to be truthy and also render
    imagekit specs (e.g. artist_detail.html).  Call super().setUp() first,
    then self._setup_media() to activate.  tearDown calls self._teardown_media().
    """

    def _setup_media(self):
        self._media_tmp = _make_test_image_dir()
        self._media_override = self.settings(MEDIA_ROOT=self._media_tmp)
        self._media_override.enable()

    def _teardown_media(self):
        self._media_override.disable()
        shutil.rmtree(self._media_tmp, ignore_errors=True)

    TEST_ARTIST_IMAGE = 'artist_images/test.jpg'


class ArtistModelTests(TestCase):
    def test_save_splits_legacy_name(self):
        artist = Artist.objects.create(
            name='Ada Lovelace',
            email='ada@example.com',
            phone='555-1212',
        )

        self.assertEqual(artist.first_name, 'Ada')
        self.assertEqual(artist.last_name, 'Lovelace')
        self.assertEqual(artist.full_name, 'Ada Lovelace')
        self.assertEqual(artist.slug, 'ada-lovelace')

    def test_save_syncs_legacy_name_from_split_fields(self):
        artist = Artist.objects.create(
            first_name='Grace',
            last_name='Hopper',
            email='grace@example.com',
            phone='555-3434',
            name='',
        )

        self.assertEqual(artist.name, 'Grace Hopper')
        self.assertEqual(str(artist), 'Grace Hopper')
        self.assertEqual(artist.slug, 'grace-hopper')

    def test_duplicate_names_get_unique_slugs(self):
        first_artist = Artist.objects.create(
            name='Ada Lovelace',
            email='ada@example.com',
            phone='555-1212',
        )
        second_artist = Artist.objects.create(
            name='Ada Lovelace',
            email='ada2@example.com',
            phone='555-3434',
        )

        self.assertEqual(first_artist.slug, 'ada-lovelace')
        self.assertEqual(second_artist.slug, 'ada-lovelace-2')

    def test_artists_are_private_by_default(self):
        artist = Artist.objects.create(
            name='Private Artist',
            email='private@example.com',
            phone='555-9999',
        )

        from gallery.models import Show
        self.assertFalse(Show.objects.filter(artworks__artists=artist).exists())


class PublicSlugNormalizationTests(TestCase):
    def test_artwork_slug_replaces_underscores_with_hyphens(self):
        artwork = Artwork.objects.create(
            name='Cobble_stone.png',
            end_year=2024,
        )

        self.assertEqual(artwork.slug, 'cobble-stonepng')

    def test_artwork_slug_replaces_underscores_inside_brackets(self):
        artwork = Artwork.objects.create(
            name='untitled [dsl_73]',
            end_year=2024,
        )

        self.assertEqual(artwork.slug, 'untitled-dsl-73')


@override_settings(
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
)
class PublicUrlTests(TestCase):
    def setUp(self):
        self.artist = Artist.objects.create(
            name='Ada Lovelace',
            first_name='Ada',
            last_name='Lovelace',
            email='ada@example.com',
            phone='555-1212',
        )
        self.show = Show.objects.create(
            name='Spring Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
            status=Show.STATUS_PUBLISHED,
        )
        self.show.curators.add(self.artist)
        self.artwork = Artwork.objects.create(
            name='Analytical Engine Study',
            end_year=1843,
            medium='Ink on paper',
        )
        self.artwork.artists.add(self.artist)
        self.artwork.shows.add(self.show)
        self.event = Event.objects.create(
            name='Opening Reception',
            show=self.show,
            date=datetime.date.today(),
            start=datetime.time(18, 0),
            end=datetime.time(20, 0),
        )

    def test_non_detail_public_routes_still_resolve(self):
        urls = [
            '/artists/',
            '/artworks/',
            '/shows/',
            '/events/',
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_search_route_is_public(self):
        response = self.client.get('/artist/search/?q=Analytical')

        self.assertEqual(response.status_code, 200)

    def test_search_form_is_visible_for_anonymous_users(self):
        response = self.client.get('/artworks/')

        self.assertContains(response, 'placeholder="Search"')

    def test_tag_filters_are_hidden_for_anonymous_users(self):
        for url in ('/artists/', '/artworks/', '/shows/', '/events/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, 'Filter by tag')

    def test_legacy_public_detail_routes_redirect_to_slug_urls(self):
        redirects = [
            (f'/artist/{self.artist.pk}/', self.artist.get_absolute_url()),
            (f'/artwork/{self.artwork.pk}/', self.artwork.get_absolute_url()),
            (f'/show/{self.show.pk}/', self.show.get_absolute_url()),
            (f'/show/placards/{self.show.pk}/', self.show.get_placards_url()),
            (f'/show/instagram/{self.show.pk}/', self.show.get_instagram_url()),
            (f'/event/{self.event.pk}/', self.event.get_absolute_url()),
        ]

        for url, destination in redirects:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, destination)

    def test_slug_public_routes_resolve(self):
        urls = [
            self.artist.get_absolute_url(),
            self.artwork.get_absolute_url(),
            self.show.get_absolute_url(),
            self.show.get_placards_url(),
            self.show.get_instagram_url(),
            self.event.get_absolute_url(),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_private_artist_is_hidden_from_anonymous_users(self):
        private_artist = Artist.objects.create(
            name='Hidden Artist',
            email='hidden@example.com',
            phone='555-0000',
        )

        list_response = self.client.get('/artists/')
        detail_response = self.client.get(private_artist.get_absolute_url())

        self.assertNotContains(list_response, private_artist.name)
        self.assertEqual(detail_response.status_code, 404)

    def test_show_latest_legacy_route_redirects_to_current_show(self):
        response = self.client.get('/show/latest')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], self.show.get_absolute_url())

    def test_site_scoped_show_latest_picks_that_sites_show(self):
        """/site/<site>/show/latest mirrors /show/latest but scoped to one site, and
        lands inside that site's URL space rather than dropping out of it."""
        from gallery.models import Site
        today = datetime.date.today()
        day = datetime.timedelta(days=1)
        site_a = Site.objects.create(name='Site A', slug='site-a', status=Site.STATUS_PUBLISHED)
        site_b = Site.objects.create(name='Site B', slug='site-b', status=Site.STATUS_PUBLISHED)
        show_a = Show.objects.create(name='A Now', start=today - day, end=today + day,
                                     status=Show.STATUS_PUBLISHED)
        show_a.sites.add(site_a)
        show_b = Show.objects.create(name='B Now', start=today - day, end=today + day,
                                     status=Show.STATUS_PUBLISHED)
        show_b.sites.add(site_b)

        r = self.client.get('/site/site-a/show/latest')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers['Location'], '/site/site-a/show/%s/' % show_a.slug)
        # The other site's show is not a candidate.
        r = self.client.get('/site/site-b/show/latest')
        self.assertEqual(r.headers['Location'], '/site/site-b/show/%s/' % show_b.slug)
        # Trailing slash is accepted too, and must not resolve as slug='latest'.
        self.assertEqual(self.client.get('/site/site-a/show/latest/').status_code, 302)

    def test_site_scoped_show_latest_falls_back_to_upcoming_then_site(self):
        from gallery.models import Site
        today = datetime.date.today()
        day = datetime.timedelta(days=1)
        site = Site.objects.create(name='Later', slug='later', status=Site.STATUS_PUBLISHED)
        r = self.client.get('/site/later/show/latest')          # no shows at all
        self.assertEqual(r.headers['Location'], site.get_absolute_url())

        upcoming = Show.objects.create(name='Soon', start=today + 10 * day,
                                       end=today + 20 * day, status=Show.STATUS_PUBLISHED)
        upcoming.sites.add(site)
        r = self.client.get('/site/later/show/latest')
        self.assertEqual(r.headers['Location'], '/site/later/show/%s/' % upcoming.slug)

    def test_show_latest_never_redirects_anonymous_to_an_invisible_show(self):
        """The detail view filters by visible_show_queryset, so redirecting to a draft
        would land the visitor on a 404."""
        from gallery.models import Site
        today = datetime.date.today()
        day = datetime.timedelta(days=1)
        site = Site.objects.create(name='Drafty', slug='drafty', status=Site.STATUS_PUBLISHED)
        draft = Show.objects.create(name='Secret', start=today - day, end=today + day,
                                    status=Show.STATUS_DRAFT)
        draft.sites.add(site)

        r = self.client.get('/site/drafty/show/latest', follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('/show/%s/' % draft.slug, r.redirect_chain[-1][0])

        staff = User.objects.create_user(
            username='drafty@example.com', email='drafty@example.com', password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        r = self.client.get('/site/drafty/show/latest')
        self.assertEqual(r.headers['Location'], '/site/drafty/show/%s/' % draft.slug)

    def test_unknown_site_slug_is_404(self):
        self.assertEqual(self.client.get('/site/no-such-site/show/latest').status_code, 404)
        self.assertEqual(self.client.get('/site/no-such-site/latest/checklist').status_code, 404)

    def test_latest_checklist_shortcuts_land_on_the_web_checklist(self):
        """A durable "current checklist" link for a venue, in either spelling."""
        from gallery.models import Site
        today = datetime.date.today()
        day = datetime.timedelta(days=1)
        site = Site.objects.create(name='Venue X', slug='venue-x', status=Site.STATUS_PUBLISHED)
        other = Site.objects.create(name='Venue Y', slug='venue-y', status=Site.STATUS_PUBLISHED)
        mine = Show.objects.create(name='Mine Now', start=today - day, end=today + day,
                                   status=Show.STATUS_PUBLISHED)
        mine.sites.add(site)
        theirs = Show.objects.create(name='Theirs Now', start=today - day, end=today + day,
                                     status=Show.STATUS_PUBLISHED)
        theirs.sites.add(other)
        target = reverse('gallery:show_checklist', kwargs={'slug': mine.slug})

        for url in ('/site/venue-x/latest/checklist',
                    '/site/venue-x/latest/checklist/',
                    '/site/venue-x/show/latest/checklist'):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 302)
                self.assertEqual(r.headers['Location'], target)

        # Scoped to the site, and the checklist itself actually renders.
        self.assertEqual(self.client.get('/site/venue-y/latest/checklist').headers['Location'],
                         reverse('gallery:show_checklist', kwargs={'slug': theirs.slug}))
        r = self.client.get('/site/venue-x/latest/checklist', follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Mine Now')

    def test_latest_show_route_still_goes_to_the_show_page(self):
        """The checklist routes must not swallow the plain latest-show redirect."""
        from gallery.models import Site
        today = datetime.date.today()
        day = datetime.timedelta(days=1)
        site = Site.objects.create(name='Venue Z', slug='venue-z', status=Site.STATUS_PUBLISHED)
        show = Show.objects.create(name='Z Now', start=today - day, end=today + day,
                                   status=Show.STATUS_PUBLISHED)
        show.sites.add(site)
        r = self.client.get('/site/venue-z/show/latest')
        self.assertEqual(r.headers['Location'], '/site/venue-z/show/%s/' % show.slug)

    def test_homepage_contains_art_gallery_json_ld(self):
        response = self.client.get(reverse('index'))
        html = response.content.decode()

        self.assertContains(response, 'application/ld+json')
        self.assertIn('"@type": "ArtGallery"', html)
        self.assertIn('"@id": "https://www.120710.art"', html)
        self.assertEqual(html.count('"@context"'), 1)

    def test_artist_detail_contains_person_json_ld_with_canonical_slug_url(self):
        response = self.client.get(self.artist.get_absolute_url())
        html = response.content.decode()

        self.assertContains(response, 'application/ld+json')
        self.assertIn('"@type": "Person"', html)
        self.assertIn(f'"@id": "http://testserver{self.artist.get_absolute_url()}"', html)
        self.assertIn(f'"url": "http://testserver{self.artist.get_absolute_url()}"', html)
        self.assertEqual(html.count('"@context"'), 1)

    def test_artwork_show_and_event_details_contain_schema_json_ld(self):
        cases = [
            (self.artwork.get_absolute_url(), 'VisualArtwork'),
            (self.show.get_absolute_url(), 'VisualArtsEvent'),
            (self.event.get_absolute_url(), 'VisualArtsEvent'),
        ]

        for url, schema_type in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                html = response.content.decode()
                self.assertContains(response, 'application/ld+json')
                self.assertIn(f'"@type": "{schema_type}"', html)
                self.assertIn(f'"url": "http://testserver{url}"', html)
                self.assertEqual(html.count('"@context"'), 1)


@override_settings(
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
)
class AuthorizationWorkflowTests(MediaImageMixin, TestCase):
    def setUp(self):
        self._setup_media()
        self.artist_user = User.objects.create_user(username='artist@example.com', email='artist@example.com', password='password123')
        self.artist = Artist.objects.create(
            user=self.artist_user,
            name='Ada Lovelace',
            first_name='Ada',
            last_name='Lovelace',
            email='artist@example.com',
            phone='',
            image=self.TEST_ARTIST_IMAGE,
        )

        self.curator_user = User.objects.create_user(username='curator@example.com', email='curator@example.com', password='password123')
        self.curator_artist = Artist.objects.create(
            user=self.curator_user,
            name='Grace Hopper',
            first_name='Grace',
            last_name='Hopper',
            email='curator@example.com',
            phone='',
        )

        self.staff_user = User.objects.create_user(username='staff@example.com', email='staff@example.com', password='password123')
        add_staff_role(self.staff_user)

        self.show = Show.objects.create(
            name='Spring Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
            status=Show.STATUS_PUBLISHED,
        )
        self.show.curators.add(self.curator_artist)

        self.event = Event.objects.create(
            name='Opening Reception',
            show=self.show,
            date=datetime.date.today(),
            start=datetime.time(18, 0),
            end=datetime.time(20, 0),
        )

        self.private_artwork = Artwork.objects.create(
            name='Private Study',
            created_by=self.artist_user,
            end_year=2024,
        )
        self.private_artwork.artists.add(self.artist)

        self.public_artwork = Artwork.objects.create(
            name='Public Study',
            created_by=self.artist_user,
            end_year=2024,
        )
        self.public_artwork.artists.add(self.artist)
        self.public_artwork.shows.add(self.show)

    def tearDown(self):
        self._teardown_media()

    def test_public_users_do_not_see_private_artworks(self):
        list_response = self.client.get(reverse('gallery:artwork_list'))
        detail_response = self.client.get(self.private_artwork.get_absolute_url())

        self.assertContains(list_response, 'Public Study')
        self.assertNotContains(list_response, 'Private Study')
        self.assertEqual(detail_response.status_code, 404)

    def test_artist_owner_can_view_private_artwork(self):
        self.client.force_login(self.artist_user)

        response = self.client.get(self.private_artwork.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Private Study')

    def test_artist_owner_legacy_private_artwork_url_redirects_to_slug(self):
        self.client.force_login(self.artist_user)

        response = self.client.get(reverse('gallery:artwork_detail', kwargs={'pk': self.private_artwork.pk}))

        self.assertRedirects(response, self.private_artwork.get_absolute_url())

    def test_curator_cannot_see_private_artworks_by_other_artists(self):
        self.client.force_login(self.curator_user)

        response = self.client.get(reverse('gallery:artwork_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Private Study')

    def test_staff_can_create_shows_but_curator_cannot(self):
        self.client.force_login(self.staff_user)
        staff_response = self.client.get(reverse('gallery:show_new'))

        self.client.force_login(self.curator_user)
        curator_response = self.client.get(reverse('gallery:show_new'))

        self.assertEqual(staff_response.status_code, 200)
        self.assertEqual(curator_response.status_code, 403)

    def test_assigned_curator_can_edit_show_and_event(self):
        self.client.force_login(self.curator_user)

        show_response = self.client.get(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}))
        event_response = self.client.get(reverse('gallery:event_edit', kwargs={'pk': self.event.pk}))

        self.assertEqual(show_response.status_code, 200)
        self.assertEqual(event_response.status_code, 200)

    def test_curator_not_on_show_cannot_edit_it(self):
        other_curator = User.objects.create_user(
            username='other-curator@example.com',
            email='other-curator@example.com',
            password='pw',
        )
        self.client.force_login(other_curator)

        response = self.client.get(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}))

        self.assertEqual(response.status_code, 403)

    def test_curator_not_on_show_cannot_delete_it(self):
        other_curator = User.objects.create_user(
            username='other-curator@example.com',
            email='other-curator@example.com',
            password='pw',
        )
        self.client.force_login(other_curator)

        response = self.client.post(reverse('gallery:show_delete', kwargs={'pk': self.show.pk}))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Show.objects.filter(pk=self.show.pk).exists())

    def test_curator_on_show_can_edit_it(self):
        other_curator = User.objects.create_user(
            username='other-curator@example.com',
            email='other-curator@example.com',
            password='pw',
        )
        other_curator_artist = Artist.objects.create(
            user=other_curator,
            name='Other Curator',
            first_name='Other',
            last_name='Curator',
            email='other-curator@example.com',
            phone='',
        )
        self.show.curators.add(other_curator_artist)
        self.client.force_login(other_curator)

        response = self.client.get(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}))

        self.assertEqual(response.status_code, 200)

    def test_curator_can_assign_artworks_to_show(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        ArtworkSubmission.objects.create(
            show=self.show,
            artwork=self.private_artwork,
            submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)

        response = self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))

        self.show.refresh_from_db()

        self.assertRedirects(response, self.show.get_absolute_url())
        self.assertTrue(self.show.artworks.filter(pk=self.private_artwork.pk).exists())

    def test_artist_can_edit_artwork_without_open_call_available_field(self):
        self.client.force_login(self.artist_user)

        response = self.client.post(reverse('gallery:artwork_edit', kwargs={'pk': self.private_artwork.pk}), {
            'name': self.private_artwork.name,
            'end_year': self.private_artwork.end_year,
            'start_year': '',
            'medium': self.private_artwork.medium or 'oil on canvas',
            'width_inches': '10',
            'height_inches': '12',
            'depth_inches': '',
            'pricing_type': self.private_artwork.pricing_type,
            'price': '',
            'replacement_cost': '',
            'is_sold': '',
            'description': self.private_artwork.description or '',
            'installation': self.private_artwork.installation or '',
            # Required management form for supplemental images formset
            'supplemental_images-TOTAL_FORMS': '0',
            'supplemental_images-INITIAL_FORMS': '0',
            'supplemental_images-MIN_NUM_FORMS': '0',
            'supplemental_images-MAX_NUM_FORMS': '1000',
        })

        self.private_artwork.refresh_from_db()

        self.assertRedirects(response, self.private_artwork.get_absolute_url())

    def test_staff_can_see_edit_and_delete_links_on_show_detail(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(self.show.get_absolute_url())

        self.assertContains(response, 'Edit')
        self.assertContains(response, 'Delete')

    def test_search_is_available_to_logged_in_users(self):
        self.client.force_login(self.artist_user)

        page_response = self.client.get('/artworks/')
        search_response = self.client.get('/artist/search/?q=Public')

        self.assertContains(page_response, 'placeholder="Search"')
        self.assertEqual(search_response.status_code, 200)

    def test_logged_in_artist_can_view_own_private_artist_detail(self):
        self.client.force_login(self.artist_user)

        response = self.client.get(self.artist.get_absolute_url())

        self.assertEqual(response.status_code, 200)

    def test_logged_in_artist_search_hides_other_private_artists(self):
        other_user = User.objects.create_user(
            username='other-artist@example.com',
            email='other-artist@example.com',
            password='password123',
        )
        other_artist = Artist.objects.create(
            user=other_user,
            name='Public Facing Name',
            first_name='Public',
            last_name='Facing Name',
            email='other-artist@example.com',
            phone='',
        )

        self.client.force_login(self.artist_user)
        response = self.client.get('/artist/search/?q=Public')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, other_artist.name)

    def test_tag_filters_are_visible_to_logged_in_users(self):
        self.client.force_login(self.artist_user)

        for url in ('/artists/', '/artworks/', '/shows/', '/events/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'tag')


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class SubmissionOnboardingTests(TestCase):
    """The first-timer path: every page states the next step, and the destination
    survives signup, email verification and the profile detour."""

    def setUp(self):
        today = datetime.date.today()
        self.show = Show.objects.create(
            name='Open Call Show', status=Show.STATUS_OPEN_CALL, submission_type='open',
            start=today + datetime.timedelta(days=30), end=today + datetime.timedelta(days=60))
        self.submit_url = reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        self.show_url = self.show.get_absolute_url()

    def _cta(self):
        import re
        body = self.client.get(self.show_url).content.decode()
        m = re.search(r'new-button" href="([^"]*)">([^<]*)</a>', body)
        return (m.group(2).strip(), m.group(1).replace('&amp;', '&')) if m else (None, None)

    def _state(self):
        """(label, hint, current tracker step) from the show page.

        The label is "Submit" in every state on purpose; what tells somebody where they
        are is the hint and the tracker, so that is what these assert on."""
        import re
        body = self.client.get(self.show_url).content.decode()
        block = body[body.find('submit-cta'):][:2000]
        label = re.search(r'new-button" href="[^"]*">([^<]*)</a>', block)
        hint = re.search(r'text-muted small mt-1 mb-0">([^<]+)<', block)
        step = re.search(r'class="now"[^>]*>\s*<span[^>]*>(\d)</span>', block)
        return (label.group(1).strip() if label else None,
                hint.group(1).strip() if hint else '',
                int(step.group(1)) if step else None)

    def test_anonymous_visitor_is_offered_a_way_in(self):
        """Previously the show page showed nothing at all to a signed-out visitor —
        the one place every open-call announcement lands. The label names neither
        audience: the submit view is login-required, so it routes to sign-in, which
        offers sign-up with the destination preserved."""
        label, url = self._cta()
        self.assertEqual(label, 'Submit')
        self.assertEqual(url, self.submit_url)

        r = self.client.get(self.submit_url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('next=', r.headers['Location'])
        body = self.client.get(r.headers['Location']).content.decode()
        self.assertIn('%s?next=' % reverse('account_signup'), body)

    def test_label_names_neither_audience(self):
        """"Sign up to submit" was wrong for everyone who already had an account."""
        body = self.client.get(self.show_url).content.decode()
        self.assertNotIn('Sign up to submit', body)
        self.assertIn('sign in or create an account', body)

    def test_the_button_says_submit_in_every_state(self):
        """Everyone who clicks it wants the same thing, so it offers the same thing.

        The intermediate steps used to be on the button — "Set up your artist profile",
        "Finish your profile (2 to go)". That read as a detour, and it meant somebody with
        an account but no profile, who is *further along* than a stranger, was the only
        reader never offered the thing they came for. The steps are the flow's problem;
        `artwork_submit` routes each state to the right one."""
        user = User.objects.create_user(
            username='led@example.com', email='led@example.com', password='pw')
        self.client.force_login(user)
        self.assertEqual(self._state()[0], 'Submit')                  # no profile

        artist = Artist.objects.create(user=user, first_name='Led', last_name='Through',
                                       email='led@example.com')
        self.assertEqual(self._state()[0], 'Submit')                  # incomplete

        artist.zipcode = '94710'
        artist.save()
        self.assertEqual(self._state()[0], 'Submit')                  # photo outstanding

        artist.image = _test_jpg('led.jpg')
        artist.save()
        self.assertEqual(self._state()[0], 'Submit')                  # ready

        art = Artwork.objects.create(name='Piece', end_year=2025)
        art.artists.add(artist)
        ArtworkSubmission.objects.create(show=self.show, artwork=art, submitted_by=user)
        # The one exception, and it is not a barrier — it reports rather than redirects.
        self.assertEqual(self._state()[0], 'Submit another work')

    def test_the_hint_and_the_tracker_say_where_you_are(self):
        """With one label everywhere, these carry the state — this is the hand-holding."""
        user = User.objects.create_user(
            username='cta@example.com', email='cta@example.com', password='pw')
        self.client.force_login(user)

        _label, hint, step = self._state()
        self.assertEqual(step, 2)
        self.assertIn('artist profile', hint)

        artist = Artist.objects.create(user=user, first_name='Cee', last_name='Tee',
                                       email='cta@example.com', zipcode='94710')
        _label, hint, step = self._state()
        self.assertEqual(step, 2)
        self.assertIn('photo', hint)

        artist.image = _test_jpg('cta.jpg')
        artist.save()
        _label, hint, step = self._state()
        self.assertEqual(step, 3)
        self.assertIn('send it in', hint)

    def test_the_tracker_starts_on_the_show_page_for_a_stranger(self):
        _label, hint, step = self._state()
        self.assertEqual(step, 1)
        self.assertIn('sign in or create an account', hint)

    def test_submitting_requires_a_photo_but_says_so_before_you_start(self):
        user = User.objects.create_user(
            username='nop@example.com', email='nop@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='No', last_name='Photo',
                                       email='nop@example.com', zipcode='94710')
        self.client.force_login(user)
        # The show page names the requirement, so nobody meets it as a surprise...
        self.assertIn('photo', self._state()[1])
        # ...and the submit page itself still refuses, on GET, before any form is
        # filled in, carrying a way back.
        r = self.client.get(self.submit_url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('image', r.headers['Location'])
        self.assertIn('next=', r.headers['Location'])

        artist.image = _test_jpg('ok.jpg')
        artist.save()
        self.assertEqual(self.client.get(self.submit_url).status_code, 200)

    def test_the_submit_url_leads_somebody_with_no_profile_to_create_one(self):
        """The gap every other test here stepped over: these all started from an artist
        who already existed, so nothing covered arriving with no profile at all — which
        is the whole of the new-artist path, since signing up returns you to this URL.

        It used to be a bare `redirect(show)`: no message, no destination, landing on a
        page whose own call to action was far below the fold."""
        user = User.objects.create_user(
            username='fresh@example.com', email='fresh@example.com', password='pw')
        self.client.force_login(user)

        response = self.client.get(self.submit_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('gallery:artist_new'), response.headers['Location'])
        # …carrying the way back, so finishing the profile returns them to submitting.
        self.assertIn('next=', response.headers['Location'])
        self.assertNotEqual(response.headers['Location'], self.show_url)

        followed = self.client.get(self.submit_url, follow=True)
        self.assertTrue([m for m in followed.context['messages']],
                        'redirected with no explanation of what happened')

    def test_creating_a_profile_returns_you_to_submitting(self):
        """The step the whole flow exists for, and the one that did not come back.

        `?next=` was honoured by the edit view but not the create view, so somebody
        finishing a half-filled profile was returned to submitting while a brand-new
        artist was dropped on their own profile page."""
        user = User.objects.create_user(
            username='back@example.com', email='back@example.com', password='pw')
        self.client.force_login(user)
        create_url = f"{reverse('gallery:artist_new')}?next={self.submit_url}"

        response = self.client.post(create_url, {
            'name': 'Comes Back', 'first_name': 'Comes', 'last_name': 'Back',
            'email': 'back@example.com', 'zipcode': '94710', 'country': 'US',
            'next': self.submit_url, 'image': _test_jpg('back.jpg')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], self.submit_url)
        self.assertEqual(self.client.get(self.submit_url).status_code, 200)

    def test_the_profile_form_says_where_you_are_and_carries_the_destination(self):
        """Same tracker and same promise as the edit form — a first-timer needs it more,
        not less."""
        user = User.objects.create_user(
            username='where@example.com', email='where@example.com', password='pw')
        self.client.force_login(user)
        body = self.client.get(
            f"{reverse('gallery:artist_new')}?next={self.submit_url}").content.decode()
        self.assertIn('straight back to submitting', body)
        self.assertIn('name="next"', body)

    def test_an_offsite_next_is_refused(self):
        """`next` is attacker-supplied; an unchecked one is an open redirect."""
        user = User.objects.create_user(
            username='evil@example.com', email='evil@example.com', password='pw')
        self.client.force_login(user)
        response = self.client.post(
            f"{reverse('gallery:artist_new')}?next=https://evil.example.com/", {
                'name': 'E V', 'first_name': 'E', 'last_name': 'V',
                'email': 'evil@example.com', 'zipcode': '94710', 'country': 'US',
                'next': 'https://evil.example.com/', 'image': _test_jpg('e.jpg')})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('evil.example.com', response.headers['Location'])

    def test_the_button_always_points_at_the_submit_url(self):
        """One destination for every state; the view decides what happens next."""
        user = User.objects.create_user(
            username='agree@example.com', email='agree@example.com', password='pw')
        self.client.force_login(user)
        self.assertEqual(self._cta()[1], self.submit_url)
        artist = Artist.objects.create(user=user, first_name='A', last_name='B',
                                       email='agree@example.com', zipcode='94710')
        self.assertEqual(self._cta()[1], self.submit_url)
        artist.image = _test_jpg('a.jpg')
        artist.save()
        self.assertEqual(self._cta()[1], self.submit_url)

    def test_the_submit_url_never_redirects_to_itself(self):
        """The button points at this view, so the view must not answer "not ready" by
        sending people back to the button — that is an infinite redirect. It briefly did,
        the moment the button's URL became the same for every state."""
        user = User.objects.create_user(
            username='loop@example.com', email='loop@example.com', password='pw')
        self.client.force_login(user)
        for state in ('no profile', 'incomplete profile'):
            with self.subTest(state=state):
                response = self.client.get(self.submit_url)
                self.assertEqual(response.status_code, 302)
                self.assertNotEqual(response.headers['Location'], self.submit_url)
                # And the place it sends them actually renders.
                self.assertEqual(
                    self.client.get(response.headers['Location']).status_code, 200)
            Artist.objects.create(user=user, first_name='L', last_name='P',
                                  email='loop@example.com')

    def test_an_uninvited_artist_is_told_so_rather_than_sent_to_build_a_profile(self):
        """Checked before the profile checks: sending somebody off to fill in a profile
        they will not be allowed to use is a worse dead end than a plain no."""
        invited_only = Show.objects.create(
            name='Invited Only', status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_INVITED,
            start=datetime.date.today() + datetime.timedelta(days=30),
            end=datetime.date.today() + datetime.timedelta(days=60))
        user = User.objects.create_user(
            username='out@example.com', email='out@example.com', password='pw')
        self.client.force_login(user)
        url = reverse('gallery:artwork_submit', kwargs={'slug': invited_only.slug})
        response = self.client.get(url, follow=True)
        self.assertIn('invitation only',
                      ' '.join(str(m) for m in response.context['messages']))
        self.assertNotIn(reverse('gallery:artist_new'),
                         [r[0] for r in response.redirect_chain][0])

    def test_a_card_offers_the_same_one_destination(self):
        """Cards had a `short_url` that could differ from the button's `url`. There is one
        url now, and it is the submit page from every surface and every state."""
        user = User.objects.create_user(
            username='card@example.com', email='card@example.com', password='pw')
        self.client.force_login(user)
        import re
        body = self.client.get(reverse('gallery:show_list')).content.decode()
        hrefs = re.findall(r'<a class="card__link new-button" href="([^"]+)"', body)
        self.assertTrue(hrefs, 'no submit button rendered on the show list')
        for href in hrefs:
            with self.subTest(href=href):
                self.assertEqual(href, self.submit_url)

    def test_the_call_to_action_comes_before_the_artworks(self):
        """It sat after the Artworks heading, past the rubric, the events and the status
        controls. An announcement's whole job is to get somebody to this button."""
        body = self.client.get(self.show_url).content.decode()
        cta = body.find('submit-cta')
        artworks = body.find('Artworks (')
        self.assertNotEqual(cta, -1)
        self.assertNotEqual(artworks, -1)
        self.assertLess(cta, artworks)

    def test_profile_detour_returns_to_the_submission(self):
        user = User.objects.create_user(
            username='ret@example.com', email='ret@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='Ree', last_name='Turn',
                                       email='ret@example.com')   # no zipcode
        self.client.force_login(user)
        bounce = self.client.get(self.submit_url)
        self.assertIn('next=', bounce.headers['Location'])
        r = self.client.post(
            reverse('gallery:artist_edit', kwargs={'pk': artist.pk}),
            {'first_name': 'Ree', 'last_name': 'Turn', 'email': 'ret@example.com',
             'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA', 'next': self.submit_url,
             'image': _test_jpg('detour.jpg')})
        self.assertEqual(r.headers['Location'], self.submit_url)

    def test_destination_survives_signup_and_email_verification(self):
        """Mandatory verification bounces the user to their inbox and back to a bare
        login page, where ?next= is long gone — the session carries it instead."""
        from allauth.account.models import EmailAddress, EmailConfirmationHMAC
        self.client.post(
            '%s?next=%s' % (reverse('account_signup'), self.submit_url),
            {'email': 'nina@example.com', 'password1': 'sup3rSecret!23',
             'password2': 'sup3rSecret!23', 'first_name': 'Nina', 'last_name': 'Newcomer'},
            follow=True)
        ea = EmailAddress.objects.get(email='nina@example.com')
        self.client.post('/accounts/confirm-email/%s/' % EmailConfirmationHMAC(ea).key,
                         follow=True)
        r = self.client.post('/accounts/login/',
                             {'login': 'nina@example.com', 'password': 'sup3rSecret!23'},
                             follow=True)
        visited = [u for u, _code in r.redirect_chain]
        self.assertTrue(any(self.submit_url in u for u in visited), visited)

    def test_a_google_signup_is_still_asked_for_a_photo(self):
        """Drives the real adapter, not a stand-in for it.

        Asserting that a function has been deleted proves the deletion, not the behaviour — a
        second copy of the import elsewhere, or a signal doing the same thing, would pass that
        and still hand the artist a monogram. This goes through the adapter allauth actually
        calls, with a sociallogin carrying a picture, and looks at what the artist ends up with.
        """
        from unittest import mock

        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from eatart.account_adapter import SocialAccountAdapter

        picture = 'https://lh3.googleusercontent.com/a/monogram=s96-c'
        user = User(username='gg@example.com', email='gg@example.com')
        account = SocialAccount(provider='google', uid='123', extra_data={
            'email': 'gg@example.com', 'given_name': 'Gina', 'family_name': 'Google',
            'picture': picture,
        })
        sociallogin = SocialLogin(user=user, account=account)

        request = self.client.request().wsgi_request
        request.session = self.client.session

        # If anything still reached for the picture this would fire.
        with mock.patch('requests.get', side_effect=AssertionError(
                'the signup fetched the Google picture')) as fetched:
            SocialAccountAdapter().save_user(request, sociallogin)

        self.assertFalse(fetched.called, 'nothing should fetch the Google picture')
        artist = Artist.objects.get(email='gg@example.com')
        self.assertFalse(artist.image, 'a Google signup must still be asked for a photo')
        # And the rest of the profile still arrives, so this did not simply break signup.
        self.assertEqual((artist.first_name, artist.last_name), ('Gina', 'Google'))

    def test_missing_catalogue_assets_reported_to_the_curator(self):
        staff = User.objects.create_user(
            username='cur@example.com', email='cur@example.com', password='pw')
        add_staff_role(staff)
        artist = Artist.objects.create(first_name='No', last_name='Assets',
                                       email='na@example.com')
        art = Artwork.objects.create(name='W', end_year=2025)
        art.artists.add(artist)
        self.show.artworks.add(art)
        self.client.force_login(staff)
        body = self.client.get(
            reverse('gallery:show_submissions', kwargs={'slug': self.show.slug})).content.decode()
        self.assertIn('Missing catalogue entries', body)
        self.assertIn('No Assets', body)
        self.assertIn('photo', body)


class InvitationSubmissionFlowTests(TestCase):
    """An invited artist gets the same guided path as an open-call one — the longest
    chain in the app, and the one where the invitation token is easiest to drop."""

    def setUp(self):
        today = datetime.date.today()
        self.show = Show.objects.create(
            name='Working Craft', status=Show.STATUS_OPEN_CALL, submission_type='invited',
            start=today + datetime.timedelta(days=60), end=today + datetime.timedelta(days=90))
        self.submit_url = reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})

    def _cta(self, client):
        body = client.get(self.show.get_absolute_url()).content.decode()
        m = re.search(r'new-button" href="([^"]*)">([^<]*)</a>', body)
        return (m.group(2).strip(), m.group(1).replace('&amp;', '&')) if m else (None, None)

    def test_brand_new_artist_can_follow_the_link_all_the_way(self):
        """Signed out with no account: the token has to survive sign-up AND the
        mandatory email-verification round trip, which lands on a bare login page."""
        from allauth.account.models import EmailAddress, EmailConfirmationHMAC
        inv = ShowInvitation.objects.create(show=self.show, email='newbie@example.com')

        r = self.client.get(inv.get_accept_url(), follow=True)
        self.assertIn('next=', r.redirect_chain[0][0])
        signup = re.search(r'href="(/accounts/signup/[^"]*next[^"]*)"',
                           r.content.decode())
        self.assertIsNotNone(signup, 'login page must carry the token into sign-up')

        self.client.post(signup.group(1).replace('&amp;', '&'),
                         {'email': 'newbie@example.com', 'password1': 'sup3rSecret!23',
                          'password2': 'sup3rSecret!23', 'first_name': 'New',
                          'last_name': 'Bie'}, follow=True)
        ea = EmailAddress.objects.get(email='newbie@example.com')
        self.client.post('/accounts/confirm-email/%s/' % EmailConfirmationHMAC(ea).key,
                         follow=True)
        r = self.client.post('/accounts/login/',
                             {'login': 'newbie@example.com', 'password': 'sup3rSecret!23'},
                             follow=True)
        visited = [u for u, _c in r.redirect_chain]
        self.assertTrue(any('accept-invite' in u for u in visited), visited)

        inv.refresh_from_db()
        self.assertIsNotNone(inv.claimed_by)
        self.assertIsNotNone(inv.artist)

        label, url = self._cta(self.client)
        # One label in every state; the invitee is offered the thing they came for.
        self.assertEqual(label, 'Submit')
        self.assertEqual(url, reverse('gallery:artwork_submit',
                                      kwargs={'slug': self.show.slug}))

    def test_existing_artist_signing_in_keeps_the_invitation(self):
        user = User.objects.create_user(
            username='ex@example.com', email='ex@example.com', password='pw123456!x')
        from allauth.account.models import EmailAddress
        EmailAddress.objects.create(user=user, email='ex@example.com',
                                    primary=True, verified=True)
        Artist.objects.create(user=user, first_name='Ex', last_name='Isting',
                              email='ex@example.com', zipcode='94710',
                              image=_test_jpg('ex.jpg'))
        inv = ShowInvitation.objects.create(show=self.show, email='ex@example.com')

        r = self.client.get(inv.get_accept_url(), follow=True)
        login_url = r.redirect_chain[0][0]
        # A browser posts the sign-in form back to the same URL, query string intact.
        self.client.post(login_url, {'login': 'ex@example.com',
                                     'password': 'pw123456!x'}, follow=True)
        inv.refresh_from_db()
        self.assertIsNotNone(inv.claimed_by)
        self.assertEqual(self._cta(self.client)[0], 'Submit')

    def test_accepting_lands_you_in_the_flow_not_on_the_show_page(self):
        """Accepting used to leave them on the show page with a button to find. Safe to
        send anyone straight to submitting now that the submit view routes every state —
        somebody with no profile is sent to make one and told why."""
        user = User.objects.create_user(
            username='straight@example.com', email='straight@example.com', password='pw')
        invitation = ShowInvitation.objects.create(show=self.show,
                                                   email='straight@example.com')
        self.client.force_login(user)
        # One request, so the messages are exactly the ones this journey produced.
        response = self.client.get(invitation.get_accept_url(), follow=True)
        hops = [url for url, _status in response.redirect_chain]
        self.assertEqual(hops[0], self.submit_url)
        # No profile, so the flow picks them up from there rather than stopping.
        self.assertIn(reverse('gallery:artist_new'), hops[1])

        said = [str(m) for m in response.context['messages']]
        self.assertIn('accepted', said[0])
        self.assertIn('artist profile', said[1])

    def test_accepting_for_a_show_that_is_closed_stays_on_the_show_page(self):
        """Otherwise "accepted" would be followed straight away by "not currently
        accepting submissions", and the show page is the more useful place to be."""
        self.show.status = Show.STATUS_IN_REVIEW
        self.show.save(update_fields=['status'])
        user = User.objects.create_user(
            username='late@example.com', email='late@example.com', password='pw')
        invitation = ShowInvitation.objects.create(show=self.show, email='late@example.com')
        self.client.force_login(user)
        response = self.client.get(invitation.get_accept_url(), follow=True)
        self.assertEqual([u for u, _s in response.redirect_chain],
                         [self.show.get_absolute_url()])
        said = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('accepted', said)
        self.assertNotIn('not currently accepting', said)

    def test_signed_in_invitee_completes_the_profile_and_submits(self):
        user = User.objects.create_user(
            username='in@example.com', email='in@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='In', last_name='Vitee',
                                       email='in@example.com')   # no zip, no photo
        inv = ShowInvitation.objects.create(show=self.show, email='in@example.com')
        self.client.force_login(user)
        self.client.get(inv.get_accept_url(), follow=True)

        self.assertEqual(self._cta(self.client)[0], 'Submit')
        r = self.client.post(
            reverse('gallery:artist_edit', kwargs={'pk': artist.pk}),
            {'first_name': 'In', 'last_name': 'Vitee', 'email': 'in@example.com',
             'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA', 'next': self.submit_url, 'image': _test_jpg('in.jpg')})
        self.assertEqual(r.headers['Location'], self.submit_url)
        self.assertEqual(self.client.get(self.submit_url).status_code, 200)

    def test_uninvited_artist_is_offered_nothing(self):
        user = User.objects.create_user(
            username='out@example.com', email='out@example.com', password='pw')
        Artist.objects.create(user=user, first_name='Out', last_name='Sider',
                              email='out@example.com', zipcode='94710',
                              image=_test_jpg('out.jpg'))
        self.client.force_login(user)
        self.assertIsNone(self._cta(self.client)[0])
        self.assertEqual(self.client.get(self.submit_url).status_code, 302)


class ArtistAddressTests(TestCase):
    """Country and postal code, which together decide whether an artist is in area.

    Before country existed, `clean_zipcode` applied a US format check unconditionally —
    and a zip code is required before submitting — so an artist outside the US could not
    save a profile, let alone enter a show. A national or global show was not expressible.
    """

    def setUp(self):
        # ArtistForm asks whether the user is staff, so it needs a real one.
        self.user = User.objects.create_user(
            username='addr@example.com', email='addr@example.com', password='pw')

    def _payload(self, **overrides):
        data = {'first_name': 'Wren', 'last_name': 'Halloway',
                'email': 'wren@example.com', 'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA',
                'street': '12 Quiet Lane', 'city': 'Berkeley', 'state': 'CA',
                'phone': '', 'website': '', 'instagram': '', 'venmo': '',
                'bio': '', 'statement': ''}
        data.update(overrides)
        return data

    def test_country_is_required(self):
        from gallery.forms import ArtistForm
        form = ArtistForm(self._payload(country=''), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('country', form.errors)

    def test_us_postal_codes_are_still_format_checked(self):
        from gallery.forms import ArtistForm
        form = ArtistForm(self._payload(zipcode='not-a-zip'), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('zipcode', form.errors)

    def test_a_non_us_postal_code_is_accepted(self):
        from gallery.forms import ArtistForm
        """The point of the change: 'EC1V 9BD' is not a US ZIP and must not be rejected."""
        form = ArtistForm(self._payload(country='GB', zipcode='EC1V 9BD'),
                          {'image': _test_jpg('gb.jpg')}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_existing_artists_default_to_the_us(self):
        artist = Artist.objects.create(name='Defaulted', email='d@example.com')
        self.assertEqual(str(artist.country), 'US')

    def test_street_address_is_never_public(self):
        """A home address is the most sensitive field on the record."""
        artist = Artist.objects.create(
            name='Private Person', email='pp@example.com',
            street='12 Quiet Lane', city='Berkeley', zipcode='94710')
        artist.image = None
        artist.save()
        body = self.client.get(artist.get_absolute_url()).content.decode()
        self.assertNotIn('12 Quiet Lane', body,
                         'an anonymous visitor must not see an artist street address')


class ArtistCreationPermissionTests(TestCase):
    """Who may create an artist profile, and whose account it gets attached to.

    Curators need to create records for artists who cannot sign up — someone a caregiver
    acts for, or anyone added to an invitation-only show directly. They were locked out:
    the view allowed creation only when you had no profile of your own, which every
    curator does, so /artist/new/ 403d for them. The "Create the artist profile first"
    link on the add-artwork-on-behalf page pointed straight at it.
    """

    def _user(self, email, **kwargs):
        return User.objects.create_user(
            username=email, email=email, password='pw', **kwargs)

    def test_user_without_a_profile_can_create_their_own(self):
        self.client.force_login(self._user('nobody@example.com'))
        self.assertEqual(self.client.get(reverse('gallery:artist_new')).status_code, 200)

    def test_plain_artist_with_a_profile_cannot_create_another(self):
        user = self._user('mine@example.com')
        Artist.objects.create(user=user, first_name='Al', last_name='Ready',
                              email='mine@example.com')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('gallery:artist_new')).status_code, 403)

    def test_curator_with_a_profile_can_create_one_for_someone_else(self):
        curator = self._user('cur@example.com', is_staff=True)
        Artist.objects.create(user=curator, first_name='Cura', last_name='Tor',
                              email='cur@example.com')
        self.client.force_login(curator)
        self.assertEqual(self.client.get(reverse('gallery:artist_new')).status_code, 200)

    def test_curator_created_profile_is_not_linked_to_the_curator(self):
        """The whole point is a record for someone who has no account.

        form_valid used to attach request.user unconditionally, so every artist a curator
        added came out owned by the curator — and on a staff form it silently overwrote
        whatever "Linked user account" said, including blank.
        """
        curator = self._user('cur2@example.com', is_staff=True)
        Artist.objects.create(user=curator, first_name='Cura', last_name='Tor',
                              email='cur2@example.com')
        self.client.force_login(curator)
        # email and image are both required by ArtistForm — a caregiver's address is
        # what the guide suggests for an artist who has none of their own.
        # _test_jpg, not a solid rectangle: profile photos are checked for being a flat
        # placeholder now, and a plain square is rejected here exactly as it is in production.
        self.client.post(reverse('gallery:artist_new'), {
            'first_name': 'Wren', 'last_name': 'Halloway',
            'email': 'caregiver@example.com', 'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA',
            'bio': '', 'statement': '', 'phone': '', 'website': '',
            'instagram': '', 'venmo': '',
            'image': _test_jpg('w.jpg'),
        })
        created = Artist.objects.filter(first_name='Wren').first()
        self.assertIsNotNone(created, 'the curator could not create the artist at all')
        self.assertIsNone(
            created.user,
            'a profile a curator creates for another artist must not be linked to the '
            'curator — that is what makes it claimable later')

    def _post_new_artist(self, first_name, **extra):
        data = {
            'first_name': first_name, 'last_name': 'Halloway',
            'email': f'{first_name.lower()}@example.com', 'country': 'US',
            'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA',
            'bio': '', 'statement': '', 'phone': '', 'website': '',
            'instagram': '', 'venmo': '',
            'image': _test_jpg(f'{first_name.lower()}.jpg'),
        }
        data.update(extra)
        self.client.post(reverse('gallery:artist_new'), data)
        return Artist.objects.filter(first_name=first_name).first()

    def test_an_admin_without_a_profile_is_not_attached_to_the_artist_they_record(self):
        """The case the old guard got backwards.

        It claimed the profile whenever the creator had none of their own, reading "no
        artist profile" as "must be making their own". An admin has no artist profile and
        is the likeliest person to be entering a record for an artist who will never have
        an account — so an admin recording a dead artist got their own login attached to
        him, and he then read as the admin's own profile everywhere it counts.
        """
        admin = self._user('admin-no-profile@example.com', is_staff=True)
        self.assertFalse(admin.artists.exists(), 'fixture: the admin has no profile')
        self.client.force_login(admin)
        created = self._post_new_artist('Albers')
        self.assertIsNotNone(created, 'the admin could not create the artist at all')
        self.assertIsNone(
            created.user,
            'an admin with no profile of their own recorded an artist and was attached to '
            'them — the field was left blank and blank has to mean nobody')
        self.assertFalse(admin.artists.exists(),
                         'the artist now shows up as the admin\'s own profile')

    def test_an_admin_can_still_deliberately_link_the_profile_to_somebody(self):
        """Blank means nobody, but the field must still work when it is filled in."""
        admin = self._user('admin-linking@example.com', is_staff=True)
        recipient = self._user('theartist@example.com')
        self.client.force_login(admin)
        created = self._post_new_artist('Anni', user=recipient.pk)
        self.assertEqual(created.user, recipient)

    def test_an_ordinary_user_creating_their_first_profile_still_gets_it(self):
        """The regression to avoid: they are not offered the field, so the view answers for
        them, and it must answer with themselves or they cannot edit what they just made."""
        user = self._user('firsttimer@example.com')
        self.client.force_login(user)
        created = self._post_new_artist('Wren')
        self.assertEqual(created.user, user)

    def test_a_non_staff_curator_is_not_shown_the_whole_user_list(self):
        """Why the field is staff-only, so a later reader does not "fix" it.

        A non-staff curator is somebody whose own artist profile curates a show, so they
        always have a profile and the view never guesses for them — they gain nothing from
        the field, and it would show them every user's email address and let them hand a
        profile to any account.
        """
        curator_artist = Artist.objects.create(
            user=self._user('cur-nostaff@example.com'), first_name='Cura',
            last_name='Tor', email='cur-nostaff@example.com')
        show = Show.objects.create(name='Curated', status=Show.STATUS_OPEN_CALL)
        show.curators.add(curator_artist)
        self.client.force_login(curator_artist.user)
        page = self.client.get(reverse('gallery:artist_new'))
        self.assertEqual(page.status_code, 200, 'a curator may create artist records')
        self.assertNotContains(page, 'Linked user account')
        created = self._post_new_artist('Josef')
        self.assertIsNone(created.user,
                          'a curator recording another artist was attached to them')

    def test_artists_page_offers_New_to_exactly_those_who_can_use_it(self):
        curator = self._user('cur3@example.com', is_staff=True)
        Artist.objects.create(user=curator, first_name='Cura', last_name='Tor',
                              email='cur3@example.com')
        artist_user = self._user('art3@example.com')
        Artist.objects.create(user=artist_user, first_name='Ann', last_name='Artist',
                              email='art3@example.com')

        self.client.force_login(curator)
        self.assertIn('new-button',
                      self.client.get(reverse('gallery:artist_list')).content.decode(),
                      'a curator should be offered New on the Artists page')
        self.client.force_login(artist_user)
        self.assertNotIn('new-button',
                         self.client.get(reverse('gallery:artist_list')).content.decode(),
                         'an artist who already has a profile should not be')



class CampaignStaffPagesTests(TestCase):
    """Compose / preview / test / send. The send guard is the point: a campaign cannot
    go out until a test has been sent since the last edit, and the pages have to say so
    rather than just disabling a button."""

    def setUp(self):
        from gallery.models import Site, Subscriber, Subscription
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 Tenth Street', city='Berkeley', state='CA', postal_code='94710')
        for i in range(3):
            Subscriber.opt_in(email='s%d@example.com' % i, first_name='Sub',
                              last_name=str(i), sites=[self.site],
                              source=Subscription.SOURCE_SUBSCRIBE_FORM)
        self.staff = User.objects.create_user(
            username='camp@example.com', email='camp@example.com', password='pw')
        add_staff_role(self.staff)
        self.client.force_login(self.staff)

    def _locmem(self):
        """Campaigns open their own Resend connection; point it at locmem for tests."""
        from unittest import mock
        return mock.patch('gallery.campaigns._connection',
                          lambda: mail.get_connection(
                              'django.core.mail.backends.locmem.EmailBackend'))

    def _draft(self, **kwargs):
        from gallery.models import Campaign
        data = {'site': self.site, 'subject': 'Spring show is open',
                'body_markdown': 'Come and **see** it.'}
        data.update(kwargs)
        return Campaign.objects.create(**data)

    def test_compose_creates_a_draft(self):
        from gallery.models import Campaign
        r = self.client.post(reverse('gallery:campaign_new'),
                             {'site': self.site.pk, 'subject': 'Hello',
                              'preheader': 'Three weeks only', 'template_name': '',
                              'body_markdown': 'Body text'}, follow=True)
        self.assertEqual(r.status_code, 200)
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.status, Campaign.STATUS_DRAFT)
        self.assertEqual(campaign.created_by, self.staff)

    def test_a_body_is_required_one_way_or_the_other(self):
        r = self.client.post(reverse('gallery:campaign_new'),
                             {'site': self.site.pk, 'subject': 'Hello',
                              'template_name': '', 'body_markdown': '   '})
        self.assertContains(r, 'either choose a template or write some Markdown')

    def test_preview_is_a_standalone_document_and_sends_nothing(self):
        from gallery.models import Subscriber
        campaign = self._draft()
        before = Subscriber.objects.count()
        r = self.client.get(reverse('gallery:campaign_preview', kwargs={'pk': campaign.pk}))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertTrue(body.lstrip().lower().startswith('<!doctype'))
        self.assertIn('see', body)
        self.assertEqual(mail.outbox, [])
        # The stand-in recipient must not become a real subscriber.
        self.assertEqual(Subscriber.objects.count(), before)

    def test_a_template_that_will_not_render_does_not_break_the_editor(self):
        campaign = self._draft(template_name='no_such_template.mjml')
        r = self.client.get(reverse('gallery:campaign_preview', kwargs={'pk': campaign.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertIn('does not render yet', r.content.decode())

    def test_send_is_refused_until_a_test_has_gone_out(self):
        campaign = self._draft()
        page = self.client.get(reverse('gallery:campaign_edit', kwargs={'pk': campaign.pk}))
        self.assertContains(page, 'Send a test to yourself first')
        self.assertContains(page, '3 subscribers')

        # A stale tab posting Send must not get through — the button being hidden is not
        # the guard.
        r = self.client.post(reverse('gallery:campaign_send', kwargs={'pk': campaign.pk}),
                             follow=True)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(mail.outbox, [])
        self.assertContains(r, 'Send a test to yourself first')

    def test_test_then_send_reaches_the_whole_list(self):
        campaign = self._draft()
        with self._locmem():
            self.client.post(reverse('gallery:campaign_send_test', kwargs={'pk': campaign.pk}),
                             {'address': 'me@example.com'}, follow=True)
            campaign.refresh_from_db()
            self.assertTrue(campaign.can_send)
            self.assertEqual(len(mail.outbox), 1)
            self.assertTrue(mail.outbox[0].subject.startswith('[TEST]'))

            mail.outbox.clear()
            self.client.post(reverse('gallery:campaign_send', kwargs={'pk': campaign.pk}),
                             follow=True)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')
        self.assertEqual(campaign.recipient_count, 3)
        self.assertEqual(len(mail.outbox), 3)
        # RFC 8058 one-click headers on every message.
        for message in mail.outbox:
            self.assertIn('List-Unsubscribe', message.extra_headers)
            self.assertEqual(message.extra_headers['List-Unsubscribe-Post'],
                             'List-Unsubscribe=One-Click')

    def test_editing_after_a_test_re_arms_the_guard(self):
        campaign = self._draft()
        with self._locmem():
            self.client.post(reverse('gallery:campaign_send_test', kwargs={'pk': campaign.pk}),
                             {'address': 'me@example.com'}, follow=True)
        campaign.refresh_from_db()
        self.assertTrue(campaign.can_send)

        self.client.post(reverse('gallery:campaign_edit', kwargs={'pk': campaign.pk}),
                         {'site': self.site.pk, 'subject': 'Changed my mind',
                          'template_name': '', 'body_markdown': 'Different words'},
                         follow=True)
        campaign.refresh_from_db()
        self.assertFalse(campaign.can_send)
        self.assertIn('changed since the last test', campaign.blocked_reason)

    def test_a_sent_campaign_becomes_a_read_only_record(self):
        campaign = self._draft()
        with self._locmem():
            self.client.post(reverse('gallery:campaign_send_test', kwargs={'pk': campaign.pk}),
                             {'address': 'me@example.com'}, follow=True)
            self.client.post(reverse('gallery:campaign_send', kwargs={'pk': campaign.pk}),
                             follow=True)
        subject = campaign.subject

        page = self.client.get(reverse('gallery:campaign_edit', kwargs={'pk': campaign.pk}))
        self.assertContains(page, 'can no longer be edited')
        self.client.post(reverse('gallery:campaign_edit', kwargs={'pk': campaign.pk}),
                         {'site': self.site.pk, 'subject': 'Rewritten history',
                          'template_name': '', 'body_markdown': 'x'})
        campaign.refresh_from_db()
        self.assertEqual(campaign.subject, subject)

    def test_both_previews_may_be_framed_by_our_own_page(self):
        """Django defaults X_FRAME_OPTIONS to DENY, which blocks same-origin framing too.

        Without the per-view exemption the iframe shows the browser's "refused to connect",
        which reads like the server being down rather than a response header — so this is worth
        a test rather than a comment.
        """
        campaign = self._draft()
        for url in (reverse('gallery:campaign_preview', kwargs={'pk': campaign.pk}),
                    reverse('gallery:campaign_template_preview')):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.headers.get('X-Frame-Options'), 'SAMEORIGIN')

    def test_the_rest_of_the_site_still_refuses_to_be_framed(self):
        """Relaxed for the previews only. Everything else keeps the default."""
        r = self.client.get(reverse('gallery:campaign_list'))
        self.assertEqual(r.headers.get('X-Frame-Options'), 'DENY')

    def test_a_template_can_be_previewed_before_any_draft_exists(self):
        """Choosing a template used to show nothing until you had committed to a draft.

        The template is not copied into the body field — it *is* the body — so the form looked
        like it had ignored the choice.
        """
        from gallery.models import Show
        show = Show.objects.create(
            name='Autumn', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 30))
        show.sites.add(self.site)

        r = self.client.get(reverse('gallery:campaign_template_preview'), {
            'site': self.site.pk, 'show': show.pk,
            'template': 'show_opening.mjml', 'subject': 'Autumn opens'})
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Autumn', body)
        self.assertIn('September', body)

    def test_previewing_a_template_writes_nothing(self):
        from gallery.models import Campaign
        self.client.get(reverse('gallery:campaign_template_preview'),
                        {'site': self.site.pk, 'template': 'show_opening.mjml'})
        self.assertFalse(Campaign.objects.exists())

    def test_a_show_template_with_no_show_yet_says_so_instead_of_rendering_blanks(self):
        r = self.client.get(reverse('gallery:campaign_template_preview'),
                            {'site': self.site.pk, 'template': 'show_opening.mjml'})
        self.assertContains(r, 'takes its content from a show')

    def test_an_empty_preview_invites_a_choice_rather_than_erroring(self):
        r = self.client.get(reverse('gallery:campaign_template_preview'))
        self.assertContains(r, 'Choose a template')

    def test_markdown_alone_previews_too(self):
        r = self.client.get(reverse('gallery:campaign_template_preview'),
                            {'site': self.site.pk, 'body': 'Hello **there**'})
        self.assertContains(r, '<strong>there</strong>')

    def test_the_preview_route_is_not_swallowed_by_the_pk_route(self):
        """'template-preview' sits before <int:pk>, and would otherwise 404 as a bad id."""
        self.assertEqual(reverse('gallery:campaign_template_preview'),
                         '/campaigns/template-preview/')

    def test_template_preview_is_staff_only(self):
        url = reverse('gallery:campaign_template_preview')
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        artist = User.objects.create_user(
            username='nope3@example.com', email='nope3@example.com', password='pw')
        self.client.force_login(artist)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_the_new_page_previews_and_explains_the_body_field(self):
        page = self.client.get(reverse('gallery:campaign_new'))
        self.assertContains(page, reverse('gallery:campaign_template_preview'))
        self.assertContains(page, 'it does not fill in the body field')

    def test_everything_a_reader_sees_is_centred(self):
        """MJML defaults mj-text to left, which left half of an email ranged left and half centred.

        Asserted on the rendered output rather than the templates, because the default is set
        once in the shell's mj-attributes and a new template inherits it silently — so a
        regression here would be a template that opts out, not one that forgets to opt in.
        """
        import re
        from gallery.models import Show
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 30))
        show.sites.add(self.site)
        for template in ('show_opening.mjml', 'show_closing.mjml', 'show_announcement.mjml'):
            with self.subTest(template=template):
                campaign = Campaign.objects.create(
                    site=self.site, show=show, subject='Full-Feel',
                    template_name=template, body_markdown='A note.')
                html = campaigns.render_preview(campaign)
                # The div MJML emits per mj-text block carries the visible alignment.
                aligns = set(re.findall(
                    r'<div style="font-family:[^"]*?text-align:(\w+)', html))
                self.assertEqual(aligns, {'center'})

    def test_a_bulleted_list_is_centred_as_a_block_but_reads_left(self):
        """Centring each item puts every bullet somewhere different, which is unreadable."""
        campaign = self._draft(body_markdown='- one\n- two')
        html = campaigns.render_preview(campaign)
        self.assertIn('display:inline-block', html)
        self.assertIn('margin:0 auto', html)

    def test_every_campaign_carries_the_venue_information(self):
        """Woven into the shell, not retyped per campaign — the difference from hand-writing them.

        Modelled on the Mailchimp announcements these replaced, which carried a "Come Visit the
        Gallery" block with hours, a phone number and a mapped address in every send.
        """
        from gallery.models import Site
        site = Site.objects.get(pk=self.site.pk)
        site.hours = 'Sundays 1–4pm, and by appointment.'
        site.phone = '341-205-1331'
        site.email = 'info@120710.art'
        site.instagram = '120710art'
        site.save()
        campaign = self._draft(site=site)

        html = campaigns.render_preview(campaign)
        self.assertIn('Come visit the gallery', html)
        self.assertIn('Sundays 1–4pm', html)
        self.assertIn('google.com/maps', html)
        self.assertIn('instagram.com/120710art', html)
        # No way to phone or write: the address and number are not published any more.
        self.assertNotIn('tel:341-205-1331', html)
        self.assertNotIn('mailto:info@120710.art', html)

    def test_a_venue_that_has_filled_in_nothing_gets_no_empty_heading(self):
        """Every line is conditional; a bare "Come visit" with nothing under it is worse than none."""
        from gallery.models import Site
        bare = Site.objects.create(name='Bare', slug='bare', status=Site.STATUS_PUBLISHED)
        html = campaigns.render_preview(self._draft(site=bare))
        self.assertNotIn('Come visit the gallery', html)

    def test_the_full_address_is_not_printed_twice(self):
        """CAN-SPAM needs it in the footer; repeating it in the visit block reads as a mistake."""
        campaign = self._draft()
        html = campaigns.render_preview(campaign)
        self.assertEqual(html.count('United States of America'), 1)

    def test_the_opening_reads_as_an_invitation_with_the_facts_on_their_own_lines(self):
        """The shape the Mailchimp announcements used, and the part people screenshot."""
        from gallery.models import Show
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 30))
        show.sites.add(self.site)
        curator = Artist.objects.create(first_name='Jules', last_name='Bachrach',
                                        email='j@example.com', instagram='juleszebra')
        show.curators.add(curator)
        Event.objects.create(name='Opening Reception', show=show,
                             date=datetime.date(2026, 7, 25),
                             start=datetime.time(16, 0), end=datetime.time(20, 0))
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Opening: Full-Feel',
            template_name='show_opening.mjml')

        html = campaigns.render_preview(campaign)
        self.assertIn('Opening: Full-Feel', html)
        self.assertIn('Join us for the opening of Full-Feel, a new exhibition curated by '
                      'Jules Bachrach', html)
        self.assertIn('instagram.com/juleszebra', html)
        for fact in ('📅', '🕓', '📍'):
            self.assertIn(fact, html)
        self.assertIn('Saturday, 25 July', html)
        self.assertIn('4–8 PM', html)

    # --- Subject lines ---

    def _show_with_opening(self):
        from gallery.models import Show
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 30))
        show.sites.add(self.site)
        Event.objects.create(name='Opening Reception', show=show,
                             date=datetime.date(2026, 7, 25),
                             start=datetime.time(16, 0), end=datetime.time(20, 0))
        return show

    def test_a_subject_fills_in_from_the_show(self):
        """The last place a mailing could contradict itself — one date in the subject, another
        three lines down."""
        from gallery.models import Campaign
        campaign = Campaign.objects.create(
            site=self.site, show=self._show_with_opening(),
            subject='Opening: {{ show.name }} — {{ opening.date|date:"l j F" }}',
            template_name='show_opening.mjml')
        self.assertEqual(campaign.rendered_subject,
                         'Opening: Full-Feel — Saturday 25 July')

    def test_the_sent_message_carries_the_filled_in_subject(self):
        """Not just the preview: the actual message, and the test send."""
        from gallery.models import Campaign, Subscriber, Subscription
        Subscriber.opt_in(email='s@example.com', sites=[self.site],
                          source=Subscription.SOURCE_SUBSCRIBE_FORM)
        campaign = Campaign.objects.create(
            site=self.site, show=self._show_with_opening(),
            subject='Opening: {{ show.name }}', template_name='show_opening.mjml')
        campaign.test_sent_at = timezone.now()
        campaign.save(update_fields=['test_sent_at'])

        mail.outbox.clear()
        with self._locmem():
            campaigns.send_campaign(campaign)
        self.assertEqual(mail.outbox[0].subject, 'Opening: Full-Feel')

        mail.outbox.clear()
        with self._locmem():
            campaigns.send_test(campaign, 'me@example.com')
        self.assertEqual(mail.outbox[0].subject, '[TEST] Opening: Full-Feel')

    def test_a_plain_subject_is_left_exactly_as_written(self):
        campaign = self._draft(subject='Just some words & an ampersand')
        self.assertEqual(campaign.rendered_subject, 'Just some words & an ampersand')

    def test_a_subject_that_will_not_render_falls_back_rather_than_failing_a_send(self):
        """The form catches a bad subject where it is made. At send time the contract is that
        rendering never raises — a stray brace must not stop nine hundred messages.

        Forced rather than contrived: Django's template language is forgiving enough at render
        time that a realistic bad subject is hard to write, and the property being pinned is the
        handling, not the input.
        """
        from unittest import mock
        from gallery.models import Campaign
        campaign = Campaign.objects.create(site=self.site, subject='Opening: {{ show.name }}')
        with mock.patch('django.template.Template.render',
                        side_effect=RuntimeError('boom')):
            self.assertEqual(campaign.rendered_subject, 'Opening: {{ show.name }}')

    def test_an_unclosed_brace_is_left_alone_rather_than_swallowed(self):
        """Django reads it as literal text, so the reader sees what was typed."""
        from gallery.models import Campaign
        campaign = Campaign.objects.create(site=self.site, subject='Half a thought {{ ')
        self.assertEqual(campaign.rendered_subject, 'Half a thought {{')

    def test_template_tags_are_refused_by_the_form(self):
        from gallery.forms import CampaignForm
        form = CampaignForm(data={'site': self.site.pk, 'subject': '{% load x %}hello',
                                  'template_name': '', 'body_markdown': 'body'})
        self.assertFalse(form.is_valid())
        self.assertIn('not', str(form.errors['subject']))

    def test_a_malformed_subject_is_a_form_error(self):
        from gallery.forms import CampaignForm
        form = CampaignForm(data={'site': self.site.pk,
                                  'subject': 'Opening: {{ show.name|nosuchfilter }}',
                                  'template_name': '', 'body_markdown': 'body'})
        self.assertFalse(form.is_valid())
        self.assertIn('will not render', str(form.errors['subject']))

    def test_an_event_always_shows_both_ends_of_its_hours(self):
        """A start time alone does not tell anybody whether they can still come at seven."""
        import datetime as dt
        from gallery.models import Event as E
        cases = {
            ((16, 0), (20, 0)): '4–8 PM',
            ((18, 30), (21, 0)): '6:30–9 PM',
            ((9, 0), (11, 30)): '9–11:30 AM',
            # Across noon the start needs its own meridiem or it reads as the wrong half of
            # the day.
            ((11, 0), (14, 0)): '11 AM–2 PM',
            ((12, 0), (13, 0)): '12–1 PM',
        }
        for (a, b), expected in cases.items():
            with self.subTest(hours=(a, b)):
                event = E(start=dt.time(*a), end=dt.time(*b))
                self.assertEqual(event.time_range, expected)

    def test_no_campaign_surface_shows_a_bare_start_time(self):
        """Subjects, bodies and the events list all take their hours from one place."""
        import re
        for path in ('templates/email/campaigns/show_opening.mjml',
                     'templates/email/campaigns/show_closing.mjml'):
            with self.subTest(path=path):
                source = open(path).read()
                self.assertNotIn('start|time', source,
                                 'use {{ event.time_range }} so both ends are shown')

    def test_the_default_subjects_carry_the_event_times(self):
        """An opening is an invitation, and "Saturday" without an hour is not one."""
        from gallery.models import Campaign
        from gallery.campaigns import template_subject
        show = self._show_with_opening()
        Event.objects.create(name='Closing Party', show=show,
                             date=datetime.date(2026, 8, 30),
                             start=datetime.time(17, 0), end=datetime.time(20, 0))

        opening = Campaign.objects.create(
            site=self.site, show=show, template_name='show_opening.mjml',
            subject=template_subject('show_opening.mjml'))
        self.assertEqual(opening.rendered_subject,
                         'Opening: Full-Feel — Saturday 25 July, 4–8 PM')

        closing = Campaign.objects.create(
            site=self.site, show=show, template_name='show_closing.mjml',
            subject=template_subject('show_closing.mjml'))
        self.assertEqual(closing.rendered_subject,
                         'Last chance: Full-Feel — last day 30 August · Closing Party 5–8 PM')

    def test_a_subject_falls_back_to_the_day_when_a_show_has_no_events(self):
        from gallery.models import Campaign, Show
        from gallery.campaigns import template_subject
        show = Show.objects.create(
            name='Quiet', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 30))
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            site=self.site, show=show, template_name='show_opening.mjml',
            subject=template_subject('show_opening.mjml'))
        self.assertEqual(campaign.rendered_subject, 'Opening: Quiet — Saturday 25 July')

    def test_each_template_offers_a_default_subject(self):
        from gallery.campaigns import CAMPAIGN_TEMPLATES, template_subject
        for name in CAMPAIGN_TEMPLATES:
            with self.subTest(name=name):
                self.assertTrue(template_subject(name),
                                f'{name} has no default subject to offer')

    def test_the_new_page_ships_the_defaults_and_shows_the_resolved_subject(self):
        page = self.client.get(reverse('gallery:campaign_new'))
        self.assertContains(page, 'Subject as it will arrive')
        self.assertContains(page, 'Opening: {{ show.name }}')

    def test_the_preview_endpoint_can_return_just_the_subject(self):
        show = self._show_with_opening()
        r = self.client.get(reverse('gallery:campaign_template_preview'), {
            'site': self.site.pk, 'show': show.pk, 'template': 'show_opening.mjml',
            'subject': 'Opening: {{ show.name }} — {{ opening.date|date:"j F" }}',
            'subject_only': '1'})
        self.assertEqual(r.content.decode().strip(), 'Opening: Full-Feel — 25 July')

    def test_a_show_at_another_venue_is_refused(self):
        """Mailing one venue's subscribers about another venue's show."""
        from gallery.forms import CampaignForm
        from gallery.models import Show, Site
        other = Site.objects.create(name='Elsewhere', slug='elsewhere',
                                    status=Site.STATUS_PUBLISHED)
        show = Show.objects.create(
            name='Not Ours', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 30))
        show.sites.add(other)

        form = CampaignForm(data={'site': self.site.pk, 'subject': 'Oops',
                                  'template_name': 'show_opening.mjml',
                                  'show': show.pk, 'body_markdown': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('is not at 120710', str(form.errors['show']))

    def test_a_show_is_listed_with_its_date_and_venue(self):
        """A name alone is not enough to pick from once there are years of them."""
        from gallery.forms import CampaignForm
        from gallery.models import Show
        show = Show.objects.create(
            name='Autumn', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 30))
        show.sites.add(self.site)
        label = CampaignForm().fields['show'].label_from_instance(show)
        self.assertEqual(label, 'Autumn — Sep 2026 · 120710')

    def test_duplicating_carries_the_content_and_nothing_about_the_send(self):
        """What "save as template" is usually reaching for: next month's, started from this one."""
        from gallery.models import Campaign
        campaign = self._draft(preheader='Three weeks only')
        with self._locmem():
            self.client.post(reverse('gallery:campaign_send_test', kwargs={'pk': campaign.pk}),
                             {'address': 'me@example.com'}, follow=True)
            self.client.post(reverse('gallery:campaign_send', kwargs={'pk': campaign.pk}),
                             follow=True)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')

        r = self.client.post(reverse('gallery:campaign_duplicate',
                                     kwargs={'pk': campaign.pk}), follow=True)
        self.assertEqual(r.status_code, 200)
        copy = Campaign.objects.exclude(pk=campaign.pk).get()

        # Everything a reader would see.
        self.assertEqual(copy.subject, campaign.subject)
        self.assertEqual(copy.preheader, 'Three weeks only')
        self.assertEqual(copy.body_markdown, campaign.body_markdown)
        self.assertEqual(copy.site, campaign.site)

        # And nothing about the send.
        self.assertEqual(copy.status, 'draft')
        self.assertIsNone(copy.sent_at)
        self.assertEqual(copy.recipient_count, 0)
        self.assertEqual(copy.deliveries.count(), 0)
        self.assertEqual(copy.created_by, self.staff)
        # The original is untouched — this is a copy, not a reopening.
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')

    def test_a_duplicate_has_to_be_tested_again_before_it_can_go_out(self):
        """The one thing that must not be inherited. The copy has never been looked at."""
        from gallery.models import Campaign
        campaign = self._draft()
        with self._locmem():
            self.client.post(reverse('gallery:campaign_send_test', kwargs={'pk': campaign.pk}),
                             {'address': 'me@example.com'}, follow=True)
        campaign.refresh_from_db()
        self.assertTrue(campaign.can_send)

        self.client.post(reverse('gallery:campaign_duplicate', kwargs={'pk': campaign.pk}))
        copy = Campaign.objects.exclude(pk=campaign.pk).get()
        self.assertIsNone(copy.test_sent_at)
        self.assertFalse(copy.can_send)
        self.assertIn('Send a test to yourself first', copy.blocked_reason)

    def test_duplicating_keeps_the_template_and_the_show(self):
        """A show mailing copied for the next show should need only the show changing."""
        from gallery.models import Campaign, Show
        show = Show.objects.create(
            name='Autumn', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 30))
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Autumn opens',
            template_name='show_opening.mjml')

        self.client.post(reverse('gallery:campaign_duplicate', kwargs={'pk': campaign.pk}))
        copy = Campaign.objects.exclude(pk=campaign.pk).get()
        self.assertEqual(copy.template_name, 'show_opening.mjml')
        self.assertEqual(copy.show, show)

    def test_the_subject_is_not_prefixed(self):
        """A "Copy of" prefix is one forgotten edit away from arriving in every inbox."""
        from gallery.models import Campaign
        campaign = self._draft()
        self.client.post(reverse('gallery:campaign_duplicate', kwargs={'pk': campaign.pk}))
        copy = Campaign.objects.exclude(pk=campaign.pk).get()
        self.assertEqual(copy.subject, 'Spring show is open')
        self.assertNotIn('Copy', copy.subject)

    def test_duplicate_is_staff_only_and_needs_a_post(self):
        from gallery.models import Campaign
        campaign = self._draft()
        url = reverse('gallery:campaign_duplicate', kwargs={'pk': campaign.pk})
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        artist = User.objects.create_user(
            username='nope2@example.com', email='nope2@example.com', password='pw')
        self.client.force_login(artist)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(Campaign.objects.count(), 1)

    def test_only_staff_get_in(self):
        campaign = self._draft()
        send = reverse('gallery:campaign_send', kwargs={'pk': campaign.pk})

        self.client.logout()
        self.assertEqual(self.client.get(reverse('gallery:campaign_list')).status_code, 302)
        self.assertEqual(self.client.post(send).status_code, 302)

        artist = User.objects.create_user(
            username='nope@example.com', email='nope@example.com', password='pw')
        self.client.force_login(artist)
        self.assertEqual(self.client.get(reverse('gallery:campaign_list')).status_code, 404)
        self.assertEqual(self.client.post(send).status_code, 404)
        self.assertEqual(mail.outbox, [])


class NetworkListDisabledTests(TestCase):
    """The reset.art list is collectable but not mailable yet.

    reset.art has no email authentication of its own — DKIM keys are per-domain and none of
    120710.art's carry over. A network-wide mailing would still leave the building, which is
    exactly the danger: it would arrive branded as a domain nobody can verify as the sender, on
    a first impression that is hard to take back.
    """

    def setUp(self):
        from gallery.models import Campaign, Site, Subscriber, Subscription
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 Tenth Street', city='Berkeley', state='CA', postal_code='94710')
        Subscriber.opt_in(email='n@example.com', sites=[None],
                          source=Subscription.SOURCE_IMPORT)
        # site=None is the network-wide list.
        self.campaign = Campaign.objects.create(
            site=None, subject='Across the network', body_markdown='Hello **all**.')
        self.campaign.test_sent_at = timezone.now()
        self.campaign.save(update_fields=['test_sent_at'])

        self.staff = User.objects.create_user(
            username='net@example.com', email='net@example.com', password='pw')
        add_staff_role(self.staff)
        self.client.force_login(self.staff)

    def test_a_network_wide_campaign_cannot_be_sent(self):
        self.assertFalse(self.campaign.list_is_sendable)
        self.assertFalse(self.campaign.can_send)
        self.assertIn('reset.art', self.campaign.blocked_reason)
        with self.assertRaises(ValueError):
            campaigns.send_campaign(self.campaign)
        self.assertEqual(mail.outbox, [])

    def test_it_cannot_be_resumed_into_sending_either(self):
        """The obvious way round a send guard is the resume path; it is closed too."""
        from gallery.models import Campaign
        Campaign.objects.filter(pk=self.campaign.pk).update(status=Campaign.STATUS_FAILED)
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.can_resume)
        with self.assertRaises(ValueError):
            campaigns.send_campaign(self.campaign, resume=True)
        self.assertEqual(mail.outbox, [])

    def test_the_page_says_why_rather_than_just_disabling_the_button(self):
        page = self.client.get(reverse('gallery:campaign_edit',
                                       kwargs={'pk': self.campaign.pk}))
        self.assertContains(page, 'cannot be mailed yet')
        self.assertContains(page, 'reset.art')

    def test_posting_send_from_a_stale_tab_is_refused(self):
        r = self.client.post(reverse('gallery:campaign_send',
                                     kwargs={'pk': self.campaign.pk}), follow=True)
        self.assertContains(r, 'cannot be mailed yet')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'draft')
        self.assertEqual(mail.outbox, [])

    def test_the_form_does_not_offer_a_list_that_cannot_be_mailed(self):
        page = self.client.get(reverse('gallery:campaign_new'))
        self.assertNotContains(page, 'Everyone (network-wide list)')
        self.assertContains(page, 'unavailable until reset.art has its own email')

    def test_people_can_still_be_collected_onto_the_list(self):
        """Only sending is blocked. The list keeps growing so it is ready when reset.art is."""
        from gallery.models import Subscription
        self.assertEqual(
            Subscription.objects.filter(site__isnull=True, is_subscribed=True).count(), 1)

    def test_one_setting_lifts_it(self):
        with self.settings(CAMPAIGN_NETWORK_LIST_ENABLED=True):
            self.campaign.refresh_from_db()
            self.assertTrue(self.campaign.list_is_sendable)
            self.assertTrue(self.campaign.can_send)
            self.assertEqual(self.campaign.blocked_reason, '')

    def test_a_venue_list_is_unaffected(self):
        from gallery.models import Campaign
        venue = Campaign.objects.create(
            site=self.site, subject='Just us', body_markdown='Hello.')
        venue.test_sent_at = timezone.now()
        venue.save(update_fields=['test_sent_at'])
        self.assertTrue(venue.can_send)


class OpeningHoursTests(TestCase):
    """Opening hours as data rather than a sentence.

    `Site.hours` is prose — "Sun 1-4p or by Appt MWF 12-6p" — which is fine for a human and
    useless to anything deciding whether a Tuesday at three is bookable. These rows are what the
    Visit page, the campaign footer, the schema.org output and eventually the visit scheduler all
    read, so that there is one answer rather than three that can drift.
    """

    def setUp(self):
        from gallery.models import Site
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            hours='Sun 1-4p or by Appt MWF 12-6p')
        self.staff = User.objects.create_user(
            username='hrs@example.com', email='hrs@example.com', password='pw')
        add_staff_role(self.staff)

    def _hours(self, weekday, start, end, by_appointment=False):
        from gallery.models import OpeningHours
        return OpeningHours.objects.create(
            site=self.site, weekday=weekday, start=datetime.time(*start),
            end=datetime.time(*end), by_appointment=by_appointment)

    def _reload(self):
        from gallery.models import Site
        return Site.objects.get(pk=self.site.pk)

    def test_prose_is_kept_until_something_structured_replaces_it(self):
        """A venue nobody has touched must read exactly as it did before."""
        self.assertEqual(self._reload().hours_display, 'Sun 1-4p or by Appt MWF 12-6p')
        self.assertFalse(self._reload().has_structured_hours)

    def test_structured_hours_win_once_they_exist(self):
        """Two descriptions of the same hours will eventually disagree, so only one can count."""
        self._hours(6, (13, 0), (16, 0))
        display = self._reload().hours_display
        self.assertEqual(display, 'Sun 1–4 PM')
        self.assertNotIn('1-4p', display)

    def test_identical_days_collapse_into_a_range(self):
        """Seven lines of the same hours is not something anybody reads."""
        for weekday in range(7):
            self._hours(weekday, (10, 0), (17, 0))
        self.assertEqual(self._reload().hours_display, 'Mon–Sun 10 AM–5 PM')

    def test_days_that_are_not_consecutive_are_listed(self):
        for weekday in (0, 2, 4):
            self._hours(weekday, (11, 0), (18, 0))
        self.assertEqual(self._reload().hours_display, 'Mon, Wed, Fri 11 AM–6 PM')

    def test_drop_in_hours_are_listed_before_appointment_ones(self):
        """Leading with "by appointment" reads like the gallery is shut."""
        for weekday in (0, 2, 4):
            self._hours(weekday, (11, 0), (18, 0), by_appointment=True)
        self._hours(6, (13, 0), (16, 0))
        display = self._reload().hours_display
        self.assertTrue(display.startswith('Sun 1–4 PM'), display)
        self.assertIn('Mon, Wed, Fri 11 AM–6 PM by appointment', display)

    def test_appointment_hours_are_not_open_to_the_public(self):
        """Telling a search engine the door is unlocked when it is not is worse than saying
        nothing."""
        self._hours(0, (11, 0), (18, 0), by_appointment=True)
        self._hours(6, (13, 0), (16, 0))
        site = self._reload()
        self.assertEqual(site.schema_opening_hours, ['Su 13:00-16:00'])
        # Monday: arrangeable, not open.
        self.assertFalse(site.is_open_on(datetime.date(2026, 8, 3)))
        self.assertTrue(site.open_periods_on(datetime.date(2026, 8, 3)))
        self.assertFalse(
            site.open_periods_on(datetime.date(2026, 8, 3), include_appointment=False))

    def test_a_closure_beats_the_usual_hours(self):
        """Otherwise the only way to shut for a week is to delete the hours and remember to
        put them back."""
        from gallery.models import SiteClosure
        self._hours(6, (13, 0), (16, 0))
        sunday = datetime.date(2026, 8, 2)
        self.assertTrue(self._reload().is_open_on(sunday))

        SiteClosure.objects.create(site=self.site, start_date=datetime.date(2026, 8, 1),
                                   end_date=datetime.date(2026, 8, 9), note='Between shows')
        site = self._reload()
        self.assertFalse(site.is_open_on(sunday))
        self.assertEqual(site.open_periods_on(sunday), [])
        self.assertEqual(site.closure_on(sunday).note, 'Between shows')

    def test_several_closures_can_overlap(self):
        """A week away inside a month between shows is ordinary, not a mistake."""
        from gallery.models import SiteClosure
        self._hours(6, (13, 0), (16, 0))
        SiteClosure.objects.create(site=self.site, start_date=datetime.date(2026, 8, 1),
                                   end_date=datetime.date(2026, 8, 31), note='Between shows')
        SiteClosure.objects.create(site=self.site, start_date=datetime.date(2026, 8, 10),
                                   end_date=datetime.date(2026, 8, 14), note='Away')
        site = self._reload()
        self.assertFalse(site.is_open_on(datetime.date(2026, 8, 2)))
        self.assertFalse(site.is_open_on(datetime.date(2026, 8, 12)))
        self.assertTrue(site.is_open_on(datetime.date(2026, 9, 6)))

    def test_being_away_can_close_appointments_without_closing_the_public_hours(self):
        """The real case: away midweek, but somebody is covering Sunday.

        Expressing that with date ranges alone would mean one Mon–Sat row per week of an
        absence, and getting one wrong closes a Sunday that was staffed.
        """
        from gallery.models import SiteClosure
        self._hours(6, (13, 0), (16, 0))                       # public, Sunday
        for weekday in (0, 2, 4):
            self._hours(weekday, (11, 0), (18, 0), by_appointment=True)

        SiteClosure.objects.create(
            site=self.site, start_date=datetime.date(2026, 8, 1),
            end_date=datetime.date(2026, 8, 31), note='Away', appointments_only=True)
        site = self._reload()

        monday, sunday = datetime.date(2026, 8, 3), datetime.date(2026, 8, 2)
        self.assertEqual(site.open_periods_on(monday), [], 'appointments should be off')
        self.assertTrue(site.is_open_on(sunday), 'Sunday is staffed and must stay open')
        self.assertEqual([b.time_range for b in site.open_periods_on(sunday)],
                         ['1–4 PM'])

    def test_a_full_closure_beats_an_overlapping_partial_one(self):
        """Otherwise a partial closure quietly reopens a venue a full one had shut."""
        from gallery.models import SiteClosure
        self._hours(6, (13, 0), (16, 0))
        SiteClosure.objects.create(site=self.site, start_date=datetime.date(2026, 8, 1),
                                   end_date=datetime.date(2026, 8, 31),
                                   note='Away', appointments_only=True)
        SiteClosure.objects.create(site=self.site, start_date=datetime.date(2026, 8, 1),
                                   end_date=datetime.date(2026, 8, 9), note='Building work')
        site = self._reload()
        self.assertFalse(site.is_open_on(datetime.date(2026, 8, 2)))
        self.assertTrue(site.is_open_on(datetime.date(2026, 8, 16)))

    def test_a_closure_includes_its_last_day(self):
        """"Closed 24–26 December" includes the 26th, or the door is locked on a day the site
        said it was open."""
        from gallery.models import SiteClosure
        closure = SiteClosure.objects.create(
            site=self.site, start_date=datetime.date(2026, 12, 24),
            end_date=datetime.date(2026, 12, 26))
        self.assertTrue(closure.covers(datetime.date(2026, 12, 26)))
        self.assertFalse(closure.covers(datetime.date(2026, 12, 27)))

    def test_a_day_with_two_openings_keeps_both(self):
        self._hours(5, (10, 0), (12, 0))
        self._hours(5, (14, 0), (17, 0))
        periods = self._reload().open_periods_on(datetime.date(2026, 8, 1))
        self.assertEqual([p.time_range for p in periods],
                         ['10 AM–12 PM', '2–5 PM'])

    def test_hours_read_the_same_as_event_times(self):
        """One implementation, so the Visit page and a campaign cannot format a time
        differently."""
        from gallery.models import Event
        block = self._hours(6, (16, 0), (20, 0))
        event = Event(start=datetime.time(16, 0), end=datetime.time(20, 0))
        self.assertEqual(block.time_range, event.time_range)

    # --- Editing them ---

    def test_staff_can_enter_hours_through_the_site_form(self):
        from gallery.models import OpeningHours
        self.client.force_login(self.staff)
        data = {
            'name': '120710', 'street': '1207 10th St', 'city': 'Berkeley', 'state': 'CA',
            'postal_code': '94710', 'country': 'US', 'status': 'published',
            'hours': 'Sun 1-4p or by Appt MWF 12-6p',
            'hours-TOTAL_FORMS': '1', 'hours-INITIAL_FORMS': '0',
            'hours-MIN_NUM_FORMS': '0', 'hours-MAX_NUM_FORMS': '1000',
            'hours-0-weekday': '6', 'hours-0-start': '13:00', 'hours-0-end': '16:00',
            'closures-TOTAL_FORMS': '0', 'closures-INITIAL_FORMS': '0',
            'closures-MIN_NUM_FORMS': '0', 'closures-MAX_NUM_FORMS': '1000',
        }
        response = self.client.post(
            reverse('gallery:site_edit', kwargs={'slug': self.site.slug}), data)
        self.assertIn(response.status_code, (200, 302))
        block = OpeningHours.objects.get(site=self.site)
        self.assertEqual((block.weekday, block.start.hour), (6, 13))

    def test_closing_before_opening_is_refused(self):
        """Far more likely a typo for 6pm–11pm than a genuine overnight opening."""
        from gallery.forms import OpeningHoursForm
        form = OpeningHoursForm(data={'weekday': 6, 'start': '18:00', 'end': '11:00'})
        self.assertFalse(form.is_valid())
        self.assertIn('after the opening time', str(form.errors))

    def test_a_closure_ending_before_it_starts_is_refused(self):
        from gallery.forms import SiteClosureForm
        form = SiteClosureForm(data={'start_date': '2026-12-26', 'end_date': '2026-12-24'})
        self.assertFalse(form.is_valid())
        self.assertIn('cannot be before', str(form.errors))

    def test_every_editable_site_field_is_reachable_through_the_form(self):
        """SiteForm names its fields, so a model field added without touching that list exists
        in the database and nowhere a person can get at it.

        That is exactly what happened to the visit-booking settings: the model had them, the
        migration created them, the tests passed, and the checkbox to switch booking on was
        not on any page.
        """
        from gallery.forms import SiteForm
        from gallery.models import Site

        automatic = {'id', 'slug', 'created_at'}
        editable = {f.name for f in Site._meta.get_fields()
                    if getattr(f, 'editable', False) and not f.auto_created
                    and f.name not in automatic}
        # As an admin: `directors` is deliberately withheld from anyone else, so a form
        # built with no user is missing it on purpose. The point of this test is that no
        # field is unreachable by *anyone*.
        staff = User.objects.create_user(username='sf@example.com', email='sf@example.com',
                                         password='pw', is_staff=True)
        self.assertEqual(editable - set(SiteForm(user=staff).fields), set())

    def test_the_booking_settings_can_actually_be_switched_on(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse('gallery:site_edit', kwargs={'slug': self.site.slug}))
        self.assertContains(page, 'Let visitors book a time')
        self.assertContains(page, 'name="visits_enabled"')

    def test_the_site_form_still_works_without_the_hours_editor(self):
        """The formsets are an addition — their absence must not block creating a site."""
        self.client.force_login(self.staff)
        response = self.client.post(reverse('gallery:site_new'), {
            'name': 'Second', 'street': '1 High St', 'city': 'Berkeley', 'state': 'CA',
            'postal_code': '94710', 'country': 'US', 'status': 'draft'})
        self.assertIn(response.status_code, (200, 302))
        from gallery.models import Site
        self.assertTrue(Site.objects.filter(name='Second').exists())

    # --- Where they show up ---

    def test_the_visit_page_shows_the_structured_hours(self):
        self._hours(6, (13, 0), (16, 0))
        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            body = self.client.get(reverse('visit')).content.decode()
        self.assertIn('Sun 1–4 PM', body)

    def test_a_campaign_footer_shows_them_too(self):
        from gallery.models import Campaign
        self._hours(6, (13, 0), (16, 0))
        campaign = Campaign.objects.create(
            site=self._reload(), subject='Hello', body_markdown='Body.')
        html = campaigns.render_preview(campaign)
        self.assertIn('Sun 1–4 PM', html)

    def test_the_search_engine_listing_stops_being_hand_maintained(self):
        """It was a hard-coded constant that could drift from the hours on the Visit page —
        and the two were separate strings kept in step by hand."""
        from eatart.schemaorg.mappers import _opening_hours
        self._hours(6, (13, 0), (16, 0))
        self._hours(0, (11, 0), (18, 0), by_appointment=True)
        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            self.assertEqual(_opening_hours(), ['Su 13:00-16:00'])

    def test_the_listing_falls_back_when_no_hours_are_entered(self):
        from eatart.schemaorg.mappers import _opening_hours
        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            self.assertEqual(_opening_hours(), ['Su 13:00-16:00'])


class RichTextInEmailTests(TestCase):
    """Rich text has to become text for an email, without losing its shape.

    `striptags` removes the tags and nothing else, so two paragraphs arrive as one run-on line:
    "<p>Street parking available</p><p>Nearby AC Transit…</p>" read as "Street parking available
    Nearby AC Transit…". Every word was there and the sense was gone.
    """

    NOTES = ('<p>Street parking available</p>\r\n'
             '<p>Nearby AC Transit bus stop at San Pablo and Gilman</p>')

    def setUp(self):
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 10th St', city='Berkeley', state='CA', postal_code='94710',
            visit_notes=self.NOTES)

    def test_the_filter_keeps_the_paragraph_breaks(self):
        from gallery.templatetags.site_tags import text_blocks
        self.assertEqual(
            text_blocks(self.NOTES),
            'Street parking available\nNearby AC Transit bus stop at San Pablo and Gilman')

    def test_it_survives_the_shapes_staff_actually_write(self):
        from gallery.templatetags.site_tags import text_blocks
        cases = {
            '<p>One</p><p>Two</p>': 'One\nTwo',
            'One<br>Two': 'One\nTwo',
            '<ul><li>One</li><li>Two</li></ul>': 'One\nTwo',
            '<p>One <strong>bold</strong> word</p>': 'One bold word',
            '<p>Ampersand &amp; entity</p>': 'Ampersand & entity',
            '': '',
            '<p></p><p>Only one</p>': 'Only one',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(text_blocks(source), expected)

    def test_the_campaign_footer_keeps_them_apart(self):
        from gallery.models import Campaign
        campaign = Campaign.objects.create(
            site=self.site, subject='Hello', body_markdown='Body.')
        html = campaigns.render_preview(campaign)
        self.assertIn('Street parking available<br />Nearby AC Transit', html)
        self.assertNotIn('availableNearby', html)
        self.assertNotIn('available Nearby', html)

    def test_a_show_description_keeps_them_too(self):
        """Truncation has to come after the breaks go in, or it collapses them again — which is
        what `truncatewords` did, silently undoing the fix."""
        from gallery.models import Campaign
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 30),
            description='<p>What happens when artists stop holding back?</p>'
                        '<p>Selected from 302 submissions.</p>')
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Opening',
            template_name='show_opening.mjml')
        html = campaigns.render_preview(campaign)
        self.assertIn('holding back?<br />Selected from 302', html)

    def test_the_visitor_confirmation_keeps_them(self):
        from django.template.loader import render_to_string
        html = render_to_string('email/visit_visitor.html',
                                {'site': self.site, 'visit': None, 'when': None})
        self.assertIn('Street parking available<br>Nearby AC Transit', html)

    def test_a_long_description_is_still_truncated(self):
        from gallery.templatetags.site_tags import text_blocks
        from django.template.defaultfilters import truncatewords_html
        from django.template.defaultfilters import linebreaksbr
        long = '<p>' + ' '.join(f'word{i}' for i in range(200)) + '</p>'
        out = truncatewords_html(linebreaksbr(text_blocks(long)), 45)
        self.assertIn('word0', out)
        self.assertNotIn('word100', out)


class EventRsvpTests(TestCase):
    """Yes, maybe or no — and the reminder that is the whole point of asking.

    One announcement three weeks out is a single shot at a date nobody has planned around yet,
    and what goes wrong between it and the night is almost always forgetting. A reminder to the
    whole list would be a second campaign; a reminder to somebody who replied is a service they
    asked for. That permission is what the RSVP buys.
    """

    def setUp(self):
        from django.utils import timezone as tz
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 10th St', city='Berkeley', state='CA', postal_code='94710',
            timezone='America/Los_Angeles', arrival_note='Ring the bell.')
        self.show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=tz.now().date(), end=tz.now().date() + datetime.timedelta(days=30))
        self.show.sites.add(self.site)
        self.event = Event.objects.create(
            name='Opening Reception', show=self.show,
            date=tz.now().date() + datetime.timedelta(days=7),
            start=datetime.time(18, 0), end=datetime.time(21, 0))
        mail.outbox.clear()

    def _reply(self, response='yes', email='ana@example.com', party='2', **extra):
        data = {'response': response, 'name': 'Ana Vidal', 'email': email,
                'party_size': party, 'note': '', 'address': ''}
        data.update(extra)
        return self.client.post(reverse('event_rsvp', kwargs={'pk': self.event.pk}),
                                data, follow=True)

    # --- Replying ---

    def test_the_show_page_offers_a_way_to_reply(self):
        """This is where most people see an event, and they will not click through to its own
        page to find a reply button that was only there."""
        page = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertIn(reverse('event_rsvp', kwargs={'pk': self.event.pk}), page)
        self.assertIn('RSVP', page)
        self.assertIn('add-to-cal', page)
        # On the same line as the date, not stacked under it.
        line = page.split(self.event.name)[1].split('</h3>')[0]
        self.assertIn('event-actions', line)
        self.assertIn(self.event.time_range, line)

    def test_a_show_card_offers_them_beside_its_next_event(self):
        """Cards are where a browsing visitor meets a show at all — an event listed there with
        no way to act on it is the click that never happens."""
        page = self.client.get(reverse('gallery:show_list')).content.decode()
        self.assertIn(self.event.name, page)
        self.assertIn(reverse('event_rsvp', kwargs={'pk': self.event.pk}), page)
        self.assertIn('event-actions', page)

    def test_a_card_whose_next_event_has_passed_offers_nothing(self):
        """get_next_event only returns future ones, so the card simply has no event line — but
        this pins that, since a change there would silently put a dead RSVP on every card."""
        from django.utils import timezone as tz
        self.event.date = tz.now().date() - datetime.timedelta(days=1)
        self.event.save(update_fields=['date'])
        page = self.client.get(reverse('gallery:show_list')).content.decode()
        self.assertNotIn(reverse('event_rsvp', kwargs={'pk': self.event.pk}), page)

    def test_both_places_render_the_same_pair(self):
        """One partial, so the thing that exists because people do not click through cannot
        look different in the two places they see it."""
        show_page = self.client.get(self.show.get_absolute_url()).content.decode()
        card_page = self.client.get(reverse('gallery:show_list')).content.decode()
        for marker in ('event-actions', 'event-actions__rsvp',
                       'add-to-cal add-to-cal--compact'):
            with self.subTest(marker=marker):
                self.assertIn(marker, show_page)
                self.assertIn(marker, card_page)

    def test_every_public_listing_offers_them(self):
        """Enumerated rather than checked one page at a time, because the failure was a page
        nobody thought to check: the home page's featured card was hand-written, so it never
        gained the reply button the shared card grew. A listing added later fails here too."""
        rsvp_url = reverse('event_rsvp', kwargs={'pk': self.event.pk})
        for name, url in (('home', '/'),
                          ('show list', reverse('gallery:show_list')),
                          ('site page', self.site.get_absolute_url())):
            with self.subTest(page=name):
                page = self.client.get(url, follow=True).content.decode()
                self.assertIn(self.event.name, page)
                self.assertIn(rsvp_url, page)
                self.assertIn('add-to-cal', page)

    def test_the_featured_card_offers_nothing_on_an_event_that_has_passed(self):
        """Unlike the others the featured card lists every event, past ones included, so it is
        the one place a dead RSVP button could appear next to last month's opening."""
        from django.utils import timezone as tz
        past = Event.objects.create(
            name='Past Talk', show=self.show,
            date=tz.now().date() - datetime.timedelta(days=2),
            start=datetime.time(19, 0), end=datetime.time(20, 0))
        page = self.client.get('/').content.decode()
        self.assertIn(past.name, page)
        self.assertNotIn(reverse('event_rsvp', kwargs={'pk': past.pk}), page)
        # The one that has not happened still does.
        self.assertIn(reverse('event_rsvp', kwargs={'pk': self.event.pk}), page)

    def test_a_past_event_on_the_show_page_offers_neither(self):
        from django.utils import timezone as tz
        self.event.date = tz.now().date() - datetime.timedelta(days=1)
        self.event.save(update_fields=['date'])
        page = self.client.get(self.show.get_absolute_url()).content.decode()
        self.assertNotIn(reverse('event_rsvp', kwargs={'pk': self.event.pk}), page)

    def test_the_reply_page_is_only_the_reply(self):
        """Reached from the show page, where the risk is that somebody meaning to reply lands on
        a full event page and loses the thread."""
        page = self.client.get(reverse('event_rsvp',
                                       kwargs={'pk': self.event.pk})).content.decode()
        self.assertIn(self.event.name, page)
        self.assertIn('Are you coming?', page)
        self.assertIn('How many of you', page)
        for _, label in __import__('gallery.models', fromlist=['x']).EventRsvp.RESPONSE_CHOICES:
            self.assertIn(label.replace("'", '&#x27;'), page)

    def test_replying_from_that_page_records_it(self):
        from gallery.models import EventRsvp
        self.client.post(reverse('event_rsvp', kwargs={'pk': self.event.pk}),
                         {'response': 'maybe', 'name': 'Ana Vidal',
                          'email': 'ana@example.com', 'party_size': '3',
                          'note': '', 'address': ''}, follow=True)
        rsvp = EventRsvp.objects.get()
        self.assertEqual((rsvp.response, rsvp.party_size), ('maybe', 3))

    def test_the_reply_page_refuses_a_past_event(self):
        from django.utils import timezone as tz
        self.event.date = tz.now().date() - datetime.timedelta(days=1)
        self.event.save(update_fields=['date'])
        r = self.client.get(reverse('event_rsvp', kwargs={'pk': self.event.pk}), follow=True)
        self.assertContains(r, 'already happened')

    def test_the_two_places_offering_the_answers_agree(self):
        """They had drifted: "can't make it" was secondary on one page and danger on the other,
        and "coming" was outlined on one and filled on the other — the same question asked twice
        in two visual languages."""
        from gallery import rsvps as engine
        from gallery.models import EventRsvp

        self._reply('yes')
        rsvp = EventRsvp.objects.get()
        event_page = self.client.get(self.event.get_absolute_url()).content.decode()
        change_page = self.client.get(reverse(
            'event_rsvp_change', kwargs={'token': engine.change_token(rsvp)})).content.decode()

        for value, label in EventRsvp.RESPONSE_CHOICES:
            with self.subTest(value=value):
                for page in (event_page, change_page):
                    self.assertIn(f'value="{value}"', page)
                    self.assertIn(label.replace("'", '&#x27;'), page)

        # And the same colour for each, which is what had drifted.
        self.assertNotIn('btn-outline-danger', change_page)
        for style in ('btn-outline-success', 'btn-outline-secondary'):
            self.assertIn(style, event_page)
            self.assertIn(style, change_page)

    def test_the_change_page_shows_which_answer_is_current(self):
        from gallery import rsvps as engine
        from gallery.models import EventRsvp
        self._reply('maybe')
        rsvp = EventRsvp.objects.get()
        page = self.client.get(reverse(
            'event_rsvp_change', kwargs={'token': engine.change_token(rsvp)})).content.decode()
        self.assertIn('aria-pressed="true"', page)

    def test_all_three_answers_are_recorded(self):
        from gallery.models import EventRsvp
        for response, email in (('yes', 'a@example.com'), ('maybe', 'b@example.com'),
                                ('no', 'c@example.com')):
            with self.subTest(response=response):
                self._reply(response, email=email)
                self.assertEqual(EventRsvp.objects.get(email=email).response, response)

    def test_a_second_reply_changes_the_first_rather_than_adding_one(self):
        """A second reply is somebody changing their mind, not a second guest — two rows would
        inflate what the gallery caters for."""
        from gallery.models import EventRsvp
        self._reply('yes', party='2')
        self._reply('no')
        self.assertEqual(EventRsvp.objects.count(), 1)
        self.assertEqual(EventRsvp.objects.get().response, 'no')

    def test_a_no_does_not_bring_guests(self):
        from gallery.models import EventRsvp
        self._reply('no', party='4')
        self.assertEqual(EventRsvp.objects.get().party_size, 1)

    def test_the_count_is_heads_not_replies(self):
        self._reply('yes', email='a@example.com', party='3')
        self._reply('yes', email='b@example.com', party='2')
        self._reply('maybe', email='c@example.com', party='4')
        self._reply('no', email='d@example.com')
        self.event.refresh_from_db()
        self.assertEqual(self.event.rsvp_count, 5)
        self.assertEqual(self.event.rsvp_maybe_count, 4)

    def test_a_small_count_is_not_shown(self):
        """"3 coming" reads as an empty room, and early in a cycle it is always 3."""
        self._reply('yes', party='3')
        self.event.refresh_from_db()
        self.assertIsNone(self.event.rsvp_count_public)
        page = self.client.get(self.event.get_absolute_url()).content.decode()
        self.assertNotIn('3 coming', page)

    def test_a_count_worth_showing_is_shown(self):
        for i in range(4):
            self._reply('yes', email=f'p{i}@example.com', party='3')
        self.event.refresh_from_db()
        self.assertEqual(self.event.rsvp_count_public, 12)
        page = self.client.get(self.event.get_absolute_url()).content.decode()
        self.assertIn('12 coming', page)

    def test_replying_confirms_by_email_whatever_the_answer(self):
        """A reply that vanishes without acknowledgement feels like it did not register."""
        for response, email in (('yes', 'a@example.com'), ('no', 'b@example.com')):
            with self.subTest(response=response):
                mail.outbox.clear()
                self._reply(response, email=email)
                self.assertEqual(len(mail.outbox), 1)
                self.assertEqual(mail.outbox[0].to, [email])

    def test_a_past_event_cannot_be_replied_to(self):
        from gallery.models import EventRsvp
        from django.utils import timezone as tz
        self.event.date = tz.now().date() - datetime.timedelta(days=1)
        self.event.save(update_fields=['date'])
        self._reply()
        self.assertEqual(EventRsvp.objects.count(), 0)

    def test_an_event_of_a_non_public_show_is_not_open_to_replies(self):
        from gallery.models import EventRsvp
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        self.assertEqual(
            self.client.post(reverse('event_rsvp', kwargs={'pk': self.event.pk}),
                             {'response': 'yes', 'name': 'A', 'email': 'a@example.com',
                              'party_size': '1', 'note': '', 'address': ''}).status_code, 404)
        self.assertEqual(EventRsvp.objects.count(), 0)

    def test_a_signed_in_visitor_is_not_asked_who_they_are(self):
        from gallery.models import EventRsvp
        user = User.objects.create_user(username='ana@example.com', email='ana@example.com',
                                        password='pw', first_name='Ana', last_name='Vidal')
        self.client.force_login(user)
        self.client.post(reverse('event_rsvp', kwargs={'pk': self.event.pk}),
                         {'response': 'yes', 'party_size': '2', 'note': '',
                          'name': 'Someone Else', 'email': 'other@example.com',
                          'address': ''}, follow=True)
        rsvp = EventRsvp.objects.get()
        self.assertEqual((rsvp.name, rsvp.email), ('Ana Vidal', 'ana@example.com'))

    # --- Changing your mind ---

    def test_the_link_in_the_email_changes_the_answer(self):
        from gallery import rsvps as engine
        from gallery.models import EventRsvp
        self._reply('yes')
        rsvp = EventRsvp.objects.get()
        url = reverse('event_rsvp_change', kwargs={'token': engine.change_token(rsvp)})

        # A GET only asks — mail clients prefetch links.
        self.client.get(url)
        self.assertEqual(EventRsvp.objects.get().response, 'yes')

        self.client.post(url, {'response': 'no'})
        self.assertEqual(EventRsvp.objects.get().response, 'no')

    def test_a_tampered_link_changes_nothing(self):
        from gallery.models import EventRsvp
        self._reply('yes')
        r = self.client.post(reverse('event_rsvp_change', kwargs={'token': 'nope'}),
                             {'response': 'no'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(EventRsvp.objects.get().response, 'yes')

    # --- The reminder, which is the point ---

    def _tomorrow(self):
        from django.utils import timezone as tz
        self.event.date = tz.now().date() + datetime.timedelta(days=1)
        self.event.save(update_fields=['date'])

    def test_yes_and_maybe_are_reminded_and_no_is_not(self):
        """A maybe has not decided, and the night before is when they will."""
        self._reply('yes', email='y@example.com')
        self._reply('maybe', email='m@example.com')
        self._reply('no', email='n@example.com')
        self._tomorrow()

        from gallery import rsvps as engine
        due = {r.email for r in engine.due_for_reminder()}
        self.assertEqual(due, {'y@example.com', 'm@example.com'})

    def test_only_events_tomorrow_are_reminded_about(self):
        from gallery import rsvps as engine
        self._reply('yes')
        self.assertEqual(list(engine.due_for_reminder()), [], 'a week away is not tomorrow')

    def test_the_command_sends_them(self):
        from io import StringIO
        from django.core.management import call_command
        self._reply('yes', email='y@example.com', party='3')
        self._tomorrow()
        mail.outbox.clear()

        out = StringIO()
        call_command('send_event_reminders', stdout=out)
        self.assertIn('Sent 1 reminder', out.getvalue())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Tomorrow', mail.outbox[0].subject)
        body = next(b for b, kind in mail.outbox[0].alternatives if kind == 'text/html')
        self.assertIn('Ring the bell.', body)
        self.assertIn('/rsvp/', body)

    def test_running_twice_does_not_remind_twice(self):
        """A cron that fires twice, or a re-run after a deploy, must not mail the same person."""
        from django.core.management import call_command
        self._reply('yes')
        self._tomorrow()
        mail.outbox.clear()

        call_command('send_event_reminders', verbosity=0)
        call_command('send_event_reminders', verbosity=0)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_failed_reminder_is_retried_rather_than_lost(self):
        """Marked after sending, not before: a reminder that never arrives is the whole failure
        this exists to prevent."""
        from unittest import mock
        from django.core.management import call_command
        from gallery.models import EventRsvp
        self._reply('yes')
        self._tomorrow()

        with mock.patch('gallery.rsvps.EmailMultiAlternatives.send',
                        side_effect=RuntimeError('mail down')):
            call_command('send_event_reminders', verbosity=0)
        self.assertIsNone(EventRsvp.objects.get().reminded_at)

        mail.outbox.clear()
        call_command('send_event_reminders', verbosity=0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNotNone(EventRsvp.objects.get().reminded_at)

    def test_changing_your_answer_earns_a_fresh_reminder(self):
        """Somebody who switches from no to yes the week before should still be reminded."""
        from gallery import rsvps as engine
        from gallery.models import EventRsvp
        self._reply('yes')
        self._tomorrow()
        engine.send_reminder(EventRsvp.objects.get())
        self.assertIsNotNone(EventRsvp.objects.get().reminded_at)

        self._reply('maybe')
        self.assertIsNone(EventRsvp.objects.get().reminded_at)

    def test_a_dry_run_sends_nothing(self):
        from io import StringIO
        from django.core.management import call_command
        from gallery.models import EventRsvp
        self._reply('yes')
        self._tomorrow()
        mail.outbox.clear()

        out = StringIO()
        call_command('send_event_reminders', '--dry-run', stdout=out)
        self.assertIn('Nothing was sent', out.getvalue())
        self.assertEqual(mail.outbox, [])
        self.assertIsNone(EventRsvp.objects.get().reminded_at)


class SubscriberSegmentTests(TestCase):
    """What somebody is here for, so a mailing can go to the people it concerns.

    Several at once on purpose: in a small scene the same person paints, buys and sits on a
    board, and one label would mean choosing which of those to stop mailing them about.
    """

    def setUp(self):
        from gallery.models import Artist, Subscriber
        self.Subscriber = Subscriber
        self.site = Site.objects.create(name='120710', slug='120710',
                                        status=Site.STATUS_PUBLISHED)
        # Typed by hand, so mixed case — the match has to survive that.
        Artist.objects.create(name='Dana Pinto', email='Dana@Example.com')
        self.join('dana@example.com')
        self.join('mo@example.com', interests=['collector'])
        self.join('lee@example.com', interests=['artist', 'funder'])
        self.join('sam@example.com')

    def join(self, email, **kwargs):
        from gallery.models import Subscriber
        subscriber, _ = Subscriber.opt_in(email=email, sites=[self.site], **kwargs)
        return subscriber

    def _segments(self, email):
        return self.Subscriber.objects.get(email=email).segments

    def test_nobody_who_said_nothing_is_a_visitor(self):
        self.assertEqual(self._segments('sam@example.com'), ['visitor'])

    def test_somebody_can_be_more_than_one_thing(self):
        self.assertEqual(self._segments('lee@example.com'), ['artist', 'funder'])

    def test_an_artist_profile_makes_somebody_an_artist(self):
        """The whole reason not to store this: an artist who joined the list before they had
        a profile becomes one the moment they do, with nothing to re-run."""
        self.assertEqual(self._segments('dana@example.com'), ['artist'])
        self.assertFalse(self.Subscriber.objects.get(email='dana@example.com').is_artist)

    def test_a_visitor_becomes_an_artist_by_getting_a_profile(self):
        from gallery.models import Artist
        self.assertEqual(self._segments('sam@example.com'), ['visitor'])
        Artist.objects.create(name='Sam', email='sam@example.com')
        self.assertEqual(self._segments('sam@example.com'), ['artist'])

    def test_resubscribing_does_not_wipe_what_is_known(self):
        """A subscribe form arrives with nothing ticked far more often than it means
        'forget what I told you'."""
        self.join('mo@example.com')
        self.assertEqual(self._segments('mo@example.com'), ['collector'])

    def test_a_campaign_with_no_segment_goes_to_everyone(self):
        from gallery import campaigns as engine
        from gallery.models import Campaign
        campaign = Campaign.objects.create(site=self.site, subject='x', body_markdown='hi')
        self.assertEqual(len(engine.recipients(campaign)), 4)

    def test_a_segmented_campaign_reaches_only_that_segment(self):
        from gallery import campaigns as engine
        from gallery.models import Campaign
        expected = {
            'artist': {'dana@example.com', 'lee@example.com'},
            'collector': {'mo@example.com'},
            'funder': {'lee@example.com'},
            'visitor': {'sam@example.com'},
        }
        for segment, emails in expected.items():
            with self.subTest(segment=segment):
                campaign = Campaign.objects.create(site=self.site, subject='x',
                                                   segment=segment, body_markdown='hi')
                got = {s.subscriber.email for s in engine.recipients(campaign)}
                self.assertEqual(got, emails)

    def test_a_segment_never_reaches_somebody_who_left(self):
        """Segmenting narrows the list; it must not be a way around is_subscribed."""
        from gallery import campaigns as engine
        from gallery.models import Campaign, Subscriber
        Subscriber.objects.get(email='mo@example.com').unsubscribe_all()
        campaign = Campaign.objects.create(site=self.site, subject='x',
                                           segment='collector', body_markdown='hi')
        self.assertEqual(list(engine.recipients(campaign)), [])

    def test_the_send_list_has_no_duplicates(self):
        """Artists are matched by a subquery against a table that may hold one address
        twice, which without distinct() would mail that person twice."""
        from gallery import campaigns as engine
        from gallery.models import Artist, Campaign
        Artist.objects.create(name='Dana again', email='dana@example.com')
        campaign = Campaign.objects.create(site=self.site, subject='x',
                                           segment='artist', body_markdown='hi')
        addresses = [s.subscriber.email for s in engine.recipients(campaign)]
        self.assertEqual(len(addresses), len(set(addresses)))

    def test_the_audience_line_says_who_it_goes_to(self):
        from gallery.models import Campaign
        plain = Campaign.objects.create(site=self.site, subject='x', body_markdown='hi')
        narrowed = Campaign.objects.create(site=self.site, subject='x', segment='collector',
                                           body_markdown='hi')
        self.assertEqual(plain.audience_label, '120710')
        self.assertEqual(narrowed.audience_label, '120710 · Collectors')

    def test_the_subscribe_form_records_interests_and_never_requires_them(self):
        from eatart.forms.subscribe import SubscribeForm
        self.assertFalse(SubscribeForm().fields['interests'].required)
        response = self.client.post(reverse('subscribe'), {
            'first_name': 'Ada', 'last_name': 'Nkem', 'email': 'ada@example.com',
            'interests': ['collector', 'funder'], 'address': ''}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._segments('ada@example.com'), ['collector', 'funder'])

    def _artist_form(self, artist, user, wants=True, **overrides):
        from gallery.forms import ArtistForm
        data = {'name': artist.name, 'first_name': artist.first_name,
                'last_name': artist.last_name, 'email': artist.email,
                'zipcode': '94710', 'country': 'US',
                'subscribe_to_mailing_list': wants}
        data.update(overrides)
        return ArtistForm(instance=artist, user=user, data=data,
                          files={'image': _test_jpg('me.jpg')})

    def test_joining_the_list_from_an_artist_profile_records_them_as_an_artist(self):
        from gallery.models import Artist, Subscriber
        user = User.objects.create_user(username='rae@example.com',
                                        email='rae@example.com', password='pw')
        artist = Artist.objects.create(user=user, name='Rae Iyer', first_name='Rae',
                                       last_name='Iyer', email='rae@example.com',
                                       zipcode='94710')
        form = self._artist_form(artist, user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        subscriber = Subscriber.objects.get(email='rae@example.com')
        self.assertTrue(subscriber.is_artist)
        self.assertEqual(subscriber.segments, ['artist'])

    def test_the_recorded_flag_outlives_a_change_of_profile_email(self):
        """Why it is recorded and not left to the directory match: that match is on the
        address, so it stops holding the moment the profile's email changes."""
        from gallery.models import Artist, Subscriber
        user = User.objects.create_user(username='moved@example.com',
                                        email='moved@example.com', password='pw')
        artist = Artist.objects.create(user=user, name='Moved On', first_name='Moved',
                                       last_name='On', email='moved@example.com',
                                       zipcode='94710')
        self._artist_form(artist, user).save()

        artist.refresh_from_db()
        artist.email = 'elsewhere@example.com'
        artist.save()

        subscriber = Subscriber.objects.get(email='moved@example.com')
        self.assertFalse(subscriber.in_artist_directory)   # derivation no longer matches
        self.assertEqual(subscriber.segments, ['artist'])  # the record still does

    def test_an_existing_collector_who_adds_a_profile_stays_a_collector(self):
        from gallery.models import Artist, Subscriber
        self.join('both@example.com', interests=['collector'])
        user = User.objects.create_user(username='both@example.com',
                                        email='both@example.com', password='pw')
        artist = Artist.objects.create(user=user, name='Both Ways', first_name='Both',
                                       last_name='Ways', email='both@example.com',
                                       zipcode='94710')
        self._artist_form(artist, user).save()
        self.assertEqual(Subscriber.objects.get(email='both@example.com').segments,
                         ['artist', 'collector'])

    def test_leaving_the_list_from_the_profile_still_works(self):
        """Unticking is a withdrawal of consent and must be honoured. The segment is a
        fact about the person rather than a permission, so it stays — is_subscribed is
        what gates sending, and recipients() applies that first."""
        from gallery import campaigns as engine
        from gallery.models import Artist, Campaign, Subscriber
        user = User.objects.create_user(username='off@example.com',
                                        email='off@example.com', password='pw')
        artist = Artist.objects.create(user=user, name='Opts Out', first_name='Opts',
                                       last_name='Out', email='off@example.com',
                                       zipcode='94710')
        self._artist_form(artist, user).save()
        artist.refresh_from_db()
        self._artist_form(artist, user, wants=False).save()

        subscriber = Subscriber.objects.get(email='off@example.com')
        self.assertFalse(any(s.is_subscribed for s in subscriber.subscriptions.all()))
        campaign = Campaign.objects.create(site=self.site, subject='x',
                                           segment='artist', body_markdown='hi')
        self.assertNotIn('off@example.com',
                         [s.subscriber.email for s in engine.recipients(campaign)])

    def test_signing_up_without_ticking_anything_still_works(self):
        response = self.client.post(reverse('subscribe'), {
            'first_name': 'Bo', 'last_name': 'Reyes', 'email': 'bo@example.com',
            'address': ''}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._segments('bo@example.com'), ['visitor'])


class SubscriberSegmentStaffTests(TestCase):
    """The staff list: seeing segments, filtering by them, and setting them by hand."""

    def setUp(self):
        from gallery.models import Artist, Subscriber
        self.site = Site.objects.create(name='120710', slug='120710',
                                        status=Site.STATUS_PUBLISHED)
        Artist.objects.create(name='Dana', email='dana@example.com')
        for email, interests in (('dana@example.com', None),
                                 ('mo@example.com', ['collector']),
                                 ('sam@example.com', None)):
            Subscriber.opt_in(email=email, sites=[self.site], interests=interests)
        self.staff = User.objects.create_user(username='s@example.com',
                                              email='s@example.com', password='pw')
        add_staff_role(self.staff)
        self.client.force_login(self.staff)

    def test_the_page_counts_each_segment(self):
        page = self.client.get(reverse('gallery:subscriber_list')).content.decode()
        self.assertIn('Artist <strong>1</strong>', page)
        self.assertIn('Collector <strong>1</strong>', page)
        self.assertIn('Visitor <strong>1</strong>', page)

    def test_filtering_by_segment(self):
        page = self.client.get(reverse('gallery:subscriber_list'),
                               {'segment': 'artist'}).content.decode()
        self.assertIn('dana@example.com', page)
        self.assertNotIn('mo@example.com', page)

    def test_one_query_for_the_artist_directory_not_one_per_row(self):
        """The annotation shadows the cached_property of the same name. If that ever stops
        working this page goes quadratic in the size of the list."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from gallery.models import Subscriber
        for i in range(30):
            Subscriber.opt_in(email=f'bulk{i}@example.com', sites=[self.site])
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse('gallery:subscriber_list'))
        # A bound well under the 33 rows on the page: per-row would be at least that many.
        self.assertLess(len(queries), 25)

    def test_staff_can_set_and_clear_interests(self):
        from gallery.models import Subscriber
        person = Subscriber.objects.get(email='sam@example.com')
        url = reverse('gallery:subscriber_interests', kwargs={'pk': person.pk})
        self.client.post(url, {'interests': ['funder', 'collector']})
        self.assertEqual(Subscriber.objects.get(pk=person.pk).segments,
                         ['collector', 'funder'])
        # Unticking removes, unlike the public form — an operator plainly means it.
        self.client.post(url, {})
        self.assertEqual(Subscriber.objects.get(pk=person.pk).segments, ['visitor'])

    def test_setting_interests_is_staff_only(self):
        from gallery.models import Subscriber
        person = Subscriber.objects.get(email='sam@example.com')
        url = reverse('gallery:subscriber_interests', kwargs={'pk': person.pk})
        self.client.logout()
        self.assertEqual(self.client.post(url, {'interests': ['funder']}).status_code, 302)
        outsider = User.objects.create_user(username='o@example.com',
                                            email='o@example.com', password='pw')
        self.client.force_login(outsider)
        self.assertEqual(self.client.post(url, {'interests': ['funder']}).status_code, 404)
        self.assertEqual(Subscriber.objects.get(pk=person.pk).segments, ['visitor'])


class RsvpDashboardTests(TestCase):
    """What curators and admins see: who replied, what past nights drew, and a CSV of it.

    Past events are half the point. A count that vanishes the morning after leaves nothing to
    compare a turnout against — not who actually came, and not what the same opening drew a
    year ago.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        from django.utils import timezone as tz
        from gallery.models import EventRsvp
        self.rsvp_model = EventRsvp
        self.site = Site.objects.create(name='120710', slug='120710',
                                        status=Site.STATUS_PUBLISHED)
        today = tz.now().date()
        self.show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=today - datetime.timedelta(days=400), end=today + datetime.timedelta(days=30))
        self.show.sites.add(self.site)
        self.soon = Event.objects.create(
            name='Opening Reception', show=self.show, date=today + datetime.timedelta(days=5),
            start=datetime.time(18, 0), end=datetime.time(21, 0))
        self.past = Event.objects.create(
            name='Last Month Opening', show=self.show, date=today - datetime.timedelta(days=20),
            start=datetime.time(18, 0), end=datetime.time(21, 0))
        for event in (self.soon, self.past):
            for i, (response, party) in enumerate(
                    [('yes', 2), ('yes', 3), ('maybe', 1), ('no', 1)]):
                self.rsvp_model.objects.create(
                    event=event, email=f'p{i}@{event.pk}.example.com', name=f'Person {i}',
                    response=response, party_size=party)
        self.curator = User.objects.create_user(username='cur@example.com',
                                                email='cur@example.com', password='pw')
        # A curator is somebody who curates a show, not somebody in a Django group named
        # 'curator' — the group check in views/visits.py was a second, disagreeing
        # definition of the role and has been removed.
        curator_artist = Artist.objects.create(
            user=self.curator, name='Cura Tor', first_name='Cura', last_name='Tor',
            email=self.curator.email)
        self.show.curators.add(curator_artist)

    def _as_curator(self):
        self.client.force_login(self.curator)

    def test_past_events_keep_their_replies(self):
        self._as_curator()
        page = self.client.get(reverse('gallery:visit_list')).content.decode()
        self.assertIn('Past events', page)
        self.assertIn(self.past.name, page)
        # And the heads, which is the number worth comparing against the door.
        self.assertIn('Last Month Opening', page.split('Past events')[1])

    def test_the_upcoming_and_past_lists_are_the_same_markup(self):
        """One partial, so a column added for tonight's opening is there for last year's too."""
        self._as_curator()
        page = self.client.get(reverse('gallery:visit_list')).content.decode()
        upcoming, past = page.split('Past events')
        for marker in ('Person 0', 'p0@', 'rsvps.csv?event='):
            with self.subTest(marker=marker):
                self.assertIn(marker, upcoming)
                self.assertIn(marker, past)

    def test_the_csv_is_gated_like_the_page(self):
        from django.contrib.auth.models import Group
        url = reverse('gallery:rsvp_csv')
        self.assertEqual(self.client.get(url).status_code, 302)  # signed out
        artist = User.objects.create_user(username='a@example.com', email='a@example.com',
                                          password='pw')
        artist.groups.add(Group.objects.get_or_create(name='artist')[0])
        self.client.force_login(artist)
        self.assertEqual(self.client.get(url).status_code, 404)
        self._as_curator()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_csv_covers_every_event_and_one_on_request(self):
        self._as_curator()
        every = self.client.get(reverse('gallery:rsvp_csv')).content.decode()
        self.assertEqual(len(every.strip().splitlines()), 9)  # header + 4 + 4
        self.assertIn(self.past.name, every)

        one = self.client.get(reverse('gallery:rsvp_csv'), {'event': self.soon.pk})
        body = one.content.decode()
        self.assertEqual(len(body.strip().splitlines()), 5)
        self.assertNotIn(self.past.name, body)
        self.assertIn('rsvps-opening-reception.csv', one['Content-Disposition'])

    def test_a_reply_cannot_smuggle_a_formula_into_a_spreadsheet(self):
        """Name and note come from a public form, and Excel runs a cell starting = or + when
        the file is opened. The whole reason this export is dangerous is that it is opened."""
        self._as_curator()
        self.rsvp_model.objects.create(event=self.soon, email='x@example.com',
                                 name='=HYPERLINK("http://evil","click")',
                                 response='yes', party_size=1, note='+1+1')
        body = self.client.get(reverse('gallery:rsvp_csv')).content.decode()
        self.assertIn('\'=HYPERLINK', body)
        self.assertIn("'+1+1", body)
        self.assertNotIn(',=HYPERLINK', body)

    def test_the_csv_is_never_cached_or_indexed(self):
        self._as_curator()
        response = self.client.get(reverse('gallery:rsvp_csv'))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertIn('attachment', response['Content-Disposition'])

    def test_a_decline_exports_no_party_size(self):
        """Same arithmetic as the page: a decline with four guests is a contradiction."""
        self._as_curator()
        body = self.client.get(reverse('gallery:rsvp_csv'),
                               {'event': self.soon.pk}).content.decode()
        declined = [ln for ln in body.splitlines() if "Can't make it" in ln][0]
        self.assertRegex(declined, r"Can't make it,Person 3,[^,]+,,")


class ShortDateTests(TestCase):
    """Dates drop the year when it is this one.

    A listing of what is on now does not need to keep saying which year now is. It stays where it
    matters — something genuinely next year, and anything printed.
    """

    def test_this_year_loses_the_year(self):
        from gallery import timeranges
        today = datetime.date(2026, 7, 31)
        self.assertEqual(timeranges.short_date(datetime.date(2026, 8, 5), today), 'Aug 5')

    def test_another_year_keeps_it(self):
        from gallery import timeranges
        today = datetime.date(2026, 7, 31)
        self.assertEqual(timeranges.short_date(datetime.date(2027, 8, 5), today), 'Aug 5, 2027')

    def test_a_run_is_compressed(self):
        from gallery import timeranges
        today = datetime.date(2026, 7, 31)
        cases = {
            ((2026, 8, 5), (2026, 8, 5)): 'Aug 5',
            ((2026, 8, 5), (2026, 8, 9)): 'Aug 5 – 9',
            ((2026, 8, 5), (2026, 9, 3)): 'Aug 5 – Sep 3',
            ((2027, 3, 1), (2027, 3, 9)): 'Mar 1 – 9, 2027',
        }
        for (start, end), expected in cases.items():
            with self.subTest(start=start, end=end):
                self.assertEqual(
                    timeranges.short_date_range(datetime.date(*start),
                                                datetime.date(*end), today), expected)

    def test_a_run_crossing_new_year_says_both(self):
        """The one case where dropping it would actively mislead."""
        from gallery import timeranges
        today = datetime.date(2026, 7, 31)
        self.assertEqual(
            timeranges.short_date_range(datetime.date(2026, 12, 20),
                                        datetime.date(2027, 1, 10), today),
            'Dec 20 – Jan 10, 2027')

    def test_the_printed_forms_keep_the_year(self):
        """A catalogue read in five years has to say which year it means, so date_range is left
        alone and the screen uses date_range_short."""
        from django.utils import timezone as tz
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=tz.now().date(), end=tz.now().date() + datetime.timedelta(days=30))
        self.assertIn(str(tz.now().year), show.date_range)
        self.assertNotIn(str(tz.now().year), show.date_range_short)

    def test_the_show_page_and_card_use_the_short_form(self):
        from django.utils import timezone as tz
        site = Site.objects.create(name='120710', slug='120710',
                                   status=Site.STATUS_PUBLISHED)
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=tz.now().date(), end=tz.now().date() + datetime.timedelta(days=30))
        show.sites.add(site)
        Event.objects.create(
            name='Opening Reception', show=show,
            date=tz.now().date() + datetime.timedelta(days=5),
            start=datetime.time(18, 0), end=datetime.time(21, 0))

        year = str(tz.now().year)
        # Every public surface, not just the two that were fixed first. The home page kept
        # printing the year for a release because its featured card was a copy of the shared
        # one rather than the shared one.
        for name, url in (('show page', show.get_absolute_url()),
                          ('show list', reverse('gallery:show_list')),
                          ('home', '/'),
                          ('site page', site.get_absolute_url())):
            with self.subTest(page=name):
                body = self.client.get(url, follow=True).content.decode()
                body = body.split('id="page-title"')[1].split('<footer')[0]
                # Only the prose. A calendar URL carries 20260806 in its dates parameter, which
                # is correct and has nothing to do with what a reader sees.
                import re as _re
                visible = _re.sub(r'<[^>]+>', ' ', _re.sub(r'href="[^"]*"', '', body))
                self.assertNotIn(year, visible)


class AddToCalendarTests(TestCase):
    """One click to put an event in somebody's own calendar.

    Two links rather than one, everywhere: Google is a URL, and a .ics file covers Apple
    Calendar, Outlook and anything else that already knows the format. No JavaScript, so the same
    two work pasted into an email as on a page.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 10th St', city='Berkeley', state='CA', postal_code='94710',
            timezone='America/Los_Angeles')
        self.show = Show.objects.create(
            name='Repetition and Repair', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 4), end=datetime.date(2026, 10, 3))
        self.show.sites.add(self.site)
        self.event = Event.objects.create(
            name='Opening Reception', show=self.show, date=datetime.date(2026, 9, 4),
            start=datetime.time(18, 0), end=datetime.time(21, 0),
            description='Come along.')

    def test_an_event_without_a_picture_borrows_the_shows(self):
        """A talk or a closing party is rarely photographed in advance, and the choice is the
        show's image or a blank card."""
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = io.BytesIO()
        Image.new('RGB', (40, 40), 'white').save(buffer, 'JPEG')
        self.show.image = SimpleUploadedFile('show.jpg', buffer.getvalue(),
                                             content_type='image/jpeg')
        self.show.save()
        self.event.refresh_from_db()

        self.assertFalse(self.event.image)
        self.assertTrue(self.event.display_image)
        self.assertEqual(self.event.display_image.name, self.show.image.name)

        page = self.client.get(self.event.get_absolute_url()).content.decode()
        self.assertIn(self.show.image.url, page)

    def test_its_own_picture_wins_when_it_has_one(self):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        for target, colour in ((self.show, 'white'), (self.event, 'black')):
            buffer = io.BytesIO()
            Image.new('RGB', (40, 40), colour).save(buffer, 'JPEG')
            target.image = SimpleUploadedFile(f'{colour}.jpg', buffer.getvalue(),
                                              content_type='image/jpeg')
            target.save()
        self.event.refresh_from_db()
        self.assertEqual(self.event.display_image.name, self.event.image.name)

    def test_neither_gives_nothing_rather_than_an_error(self):
        """`.url` on an unset ImageField raises, so the property has to answer None."""
        self.assertFalse(self.show.image)
        self.assertFalse(self.event.image)
        self.assertIsNone(self.event.display_image)
        # And the page renders without it.
        self.assertEqual(self.client.get(self.event.get_absolute_url()).status_code, 200)

    def test_the_google_link_carries_an_absolute_instant(self):
        """A naive time is read in the *reader's* zone, so a six o'clock opening in Berkeley
        would land at six o'clock for somebody in New York."""
        url = self.event.google_calendar_url()
        self.assertIn('calendar.google.com', url)
        self.assertIn('action=TEMPLATE', url)
        # 18:00 Pacific on 4 September is 01:00 UTC on the 5th.
        self.assertIn('20260905T010000Z', url)
        self.assertIn('20260905T040000Z', url)
        self.assertIn('Opening+Reception', url)

    def test_the_ics_download_is_one_event(self):
        response = self.client.get(reverse('gallery:event_ics',
                                           kwargs={'pk': self.event.pk}))
        body = response.content.decode()
        self.assertEqual(body.count('BEGIN:VEVENT'), 1)
        self.assertIn('SUMMARY:Opening Reception', body)
        self.assertIn('DTSTART:20260905T010000Z', body)
        self.assertIn('1207 10th St', body)
        self.assertIn('METHOD:PUBLISH', body)

    def test_the_ics_is_an_attachment_not_a_subscription(self):
        """The opposite of /shows.ics: a copy of one event to keep, so a Content-Disposition is
        exactly right here and exactly wrong there."""
        response = self.client.get(reverse('gallery:event_ics',
                                           kwargs={'pk': self.event.pk}))
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('text/calendar', response['Content-Type'])

    def test_an_event_of_a_non_public_show_is_not_downloadable(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        self.assertEqual(
            self.client.get(reverse('gallery:event_ics',
                                    kwargs={'pk': self.event.pk})).status_code, 404)

    def test_the_event_page_says_which_calendar_each_one_is(self):
        """There is room here to name them, and which calendar is the thing being chosen
        between. The agenda gets the short form instead — see the next test."""
        page = self.client.get(self.event.get_absolute_url()).content.decode()
        self.assertIn('calendar.google.com', page)
        self.assertIn('Add to Google Calendar', page)
        self.assertIn('Apple, Outlook or other (.ics)', page)
        self.assertIn(reverse('gallery:event_ics', kwargs={'pk': self.event.pk}), page)

    def test_nothing_is_offered_for_an_event_that_has_happened(self):
        """Adding last March's opening to your calendar is clutter, not an offer.

        Checked on every surface rather than one: the partial guards itself so a surface added
        later cannot forget, and the call sites guard too so no empty wrapper is left behind.
        """
        from django.utils import timezone as tz
        self.event.date = tz.now().date() - datetime.timedelta(days=2)
        self.event.save(update_fields=['date'])
        ics = reverse('gallery:event_ics', kwargs={'pk': self.event.pk})

        for name, url in (('event page', self.event.get_absolute_url()),
                          ('show page', self.show.get_absolute_url()),
                          ('agenda', reverse('gallery:calendar'))):
            with self.subTest(page=name):
                page = self.client.get(url).content.decode()
                self.assertNotIn('add-to-cal', page)
                self.assertNotIn(f'href="{ics}"', page)

    def test_a_mailing_offers_nothing_for_an_event_that_has_happened(self):
        """A mailing can be read long after it was sent."""
        from django.utils import timezone as tz
        from gallery.models import Campaign
        self.event.date = tz.now().date() - datetime.timedelta(days=2)
        self.event.save(update_fields=['date'])
        campaign = Campaign.objects.create(
            site=self.site, show=self.show, subject='Opening',
            template_name='show_opening.mjml')
        html = campaigns.render_preview(campaign)
        self.assertNotIn('Add to calendar', html)
        self.assertNotIn('Let us know you are coming', html)

    def test_a_mailing_about_an_event_today_still_offers_them(self):
        """The closing mailing goes out the morning of the closing — today is not past."""
        from django.utils import timezone as tz
        from gallery.models import Campaign
        self.event.date = tz.now().date()
        self.event.save(update_fields=['date'])
        campaign = Campaign.objects.create(
            site=self.site, show=self.show, subject='Tonight',
            template_name='show_opening.mjml')
        html = campaigns.render_preview(campaign)
        self.assertIn('Add to calendar', html)
        self.assertIn('Let us know you are coming', html)

    def test_the_mailing_links_to_the_page_that_is_only_the_reply(self):
        """The mailing already described the event, so an event page adds context they just read."""
        from gallery.models import Campaign
        campaign = Campaign.objects.create(
            site=self.site, show=self.show, subject='Opening',
            template_name='show_opening.mjml')
        html = campaigns.render_preview(campaign)
        self.assertIn(reverse('event_rsvp', kwargs={'pk': self.event.pk}), html)

    def test_the_agenda_uses_the_short_form(self):
        """It repeats on every future row, where a sentence would swamp the event."""
        from django.utils import timezone as tz
        future = Event.objects.create(
            name='Artist Talk', show=self.show,
            date=tz.now().date() + datetime.timedelta(days=7),
            start=datetime.time(19, 0), end=datetime.time(20, 30))
        page = self.client.get(reverse('gallery:calendar')).content.decode()
        self.assertIn('add-to-cal--compact', page)
        self.assertNotIn('Apple, Outlook or other', page)
        self.assertIn(reverse('gallery:event_ics', kwargs={'pk': future.pk}), page)

    def test_the_control_names_itself_for_a_screen_reader(self):
        """The visible label is short and the icon carries the rest, so the accessible name has
        to say which calendar each link goes to."""
        page = self.client.get(self.event.get_absolute_url()).content.decode()
        self.assertIn('aria-label="Add Opening Reception to Google Calendar"', page)
        self.assertIn('aria-hidden="true"', page)   # the glyph is decorative

    def test_the_agenda_offers_them_on_what_is_still_to_come(self):
        future = Event.objects.create(
            name='Artist Talk', show=self.show,
            date=timezone.now().date() + datetime.timedelta(days=7),
            start=datetime.time(19, 0), end=datetime.time(20, 30))
        page = self.client.get(reverse('gallery:calendar')).content.decode()
        self.assertIn('add-to-cal', page)
        self.assertIn(reverse('gallery:event_ics', kwargs={'pk': future.pk}), page)

    def test_a_campaign_carries_both_links_absolutely(self):
        """Read in a mail client, which has no page to resolve a relative URL against."""
        from gallery.models import Campaign
        campaign = Campaign.objects.create(
            site=self.site, show=self.show, subject='Opening',
            template_name='show_opening.mjml')
        html = campaigns.render_preview(campaign)
        self.assertIn('Add to calendar', html)
        self.assertIn('calendar.google.com', html)
        self.assertIn(f'/event/{self.event.pk}.ics', html)
        self.assertNotIn('href="/event/', html)

    def test_a_show_with_no_events_offers_nothing_rather_than_a_dead_link(self):
        from gallery.models import Campaign
        Event.objects.all().delete()
        campaign = Campaign.objects.create(
            site=self.site, show=self.show, subject='Opening',
            template_name='show_opening.mjml')
        html = campaigns.render_preview(campaign)
        self.assertNotIn('Add to calendar', html)

    def test_one_implementation_of_a_time_span(self):
        """Event and OpeningHours share it — a duplicate crept in and was dead code, because
        Python keeps the last definition and the two happened to agree."""
        from gallery import timeranges
        self.assertEqual(self.event.time_range,
                         timeranges.time_range(self.event.start, self.event.end))


class PlaceholderPhotoTests(TestCase):
    """A monogram is not a profile photo.

    The requirement exists so nobody is chased for a photo after acceptance. One satisfied by a
    coloured square with a letter on it costs exactly the same chasing, except the form said it
    was fine and the problem surfaces when the catalogue prints.
    """

    def _jpeg(self, image, quality=85):
        import io
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=quality)
        buffer.seek(0)
        return buffer

    def _monogram(self, colour='#1a73e8', letter='A'):
        """What Google serves for an account that has never set a picture."""
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (600, 600), colour)
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 320)
        except Exception:   # noqa: BLE001 — any font will do; the shape is what matters
            font = ImageFont.load_default()
        draw.text((300, 300), letter, fill='white', anchor='mm', font=font)
        return image

    def _portrait(self, background='#f2f0ec', noise=60000):
        """A head against a plain wall — the case a flatness test must not reject."""
        import random
        from PIL import Image, ImageDraw
        random.seed(4)
        image = Image.new('RGB', (600, 600), background)
        draw = ImageDraw.Draw(image)
        draw.ellipse((190, 140, 410, 420), fill='#9a7050')
        draw.ellipse((140, 390, 460, 700), fill='#39506e')
        pixels = image.load()
        for _ in range(noise):
            x, y = random.randrange(600), random.randrange(600)
            r, g, b = pixels[x, y]
            pixels[x, y] = tuple(max(0, min(255, v + random.randint(-28, 28)))
                                 for v in (r, g, b))
        return image

    def test_a_monogram_is_recognised(self):
        from gallery.photos import looks_like_placeholder
        for colour, letter in (('#1a73e8', 'A'), ('#d93025', 'S'), ('#188038', 'M')):
            with self.subTest(colour=colour):
                self.assertTrue(
                    looks_like_placeholder(self._jpeg(self._monogram(colour, letter))))

    def test_a_flat_colour_is_recognised(self):
        from PIL import Image
        from gallery.photos import looks_like_placeholder
        self.assertTrue(looks_like_placeholder(
            self._jpeg(Image.new('RGB', (600, 600), 'white'))))

    def test_a_portrait_against_a_plain_wall_is_not_rejected(self):
        """The one that matters: a false positive tells somebody their real photo is fake."""
        from gallery.photos import looks_like_placeholder
        for background in ('#f2f0ec', '#c8c4bd', '#7d6a55'):
            with self.subTest(background=background):
                self.assertFalse(
                    looks_like_placeholder(self._jpeg(self._portrait(background))))

    def test_an_unreadable_file_is_given_the_benefit_of_the_doubt(self):
        """Refusing a photo because it could not be measured is the worse mistake; the field's
        own validation will catch a broken file."""
        import io
        from gallery.photos import looks_like_placeholder
        self.assertFalse(looks_like_placeholder(io.BytesIO(b'not an image')))

    # --- Where it is applied ---

    def test_uploading_a_placeholder_is_refused_with_a_reason(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from gallery.forms import ArtistForm

        upload = SimpleUploadedFile('me.jpg', self._jpeg(self._monogram()).read(),
                                    content_type='image/jpeg')
        user = User.objects.create_user(username='ph@example.com', email='ph@example.com',
                                        password='pw')
        form = ArtistForm(data={'first_name': 'Ana', 'last_name': 'Vidal',
                                'email': 'ana@example.com', 'country': 'US',
                                'zipcode': '94710'},
                          files={'image': upload}, user=user)
        self.assertFalse(form.is_valid())
        self.assertIn('looks like a placeholder', str(form.errors['image']))

    def test_uploading_a_real_photo_is_accepted(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from gallery.forms import ArtistForm

        upload = SimpleUploadedFile('me.jpg', self._jpeg(self._portrait()).read(),
                                    content_type='image/jpeg')
        user = User.objects.create_user(username='ph2@example.com', email='ph2@example.com',
                                        password='pw')
        form = ArtistForm(data={'first_name': 'Ana', 'last_name': 'Vidal',
                                'email': 'ana@example.com', 'country': 'US',
                                'zipcode': '94710'},
                          files={'image': upload}, user=user)
        self.assertNotIn('image', form.errors)

    def test_the_command_finds_and_can_clear_them(self):
        from io import StringIO
        from django.core.files.base import ContentFile
        from django.core.management import call_command

        bad = Artist.objects.create(first_name='Mona', last_name='Gram',
                                    email='m@example.com')
        bad.image.save('a.jpg', ContentFile(self._jpeg(self._monogram()).read()), save=True)
        good = Artist.objects.create(first_name='Real', last_name='Person',
                                     email='r@example.com')
        good.image.save('b.jpg', ContentFile(self._jpeg(self._portrait()).read()), save=True)

        out = StringIO()
        call_command('find_placeholder_photos', stdout=out)
        report = out.getvalue()
        self.assertIn('Mona Gram', report)
        self.assertNotIn('Real Person', report)
        self.assertIn('Re-run with --clear', report)
        bad.refresh_from_db()
        self.assertTrue(bad.image, 'a report must not change anything')

        out = StringIO()
        call_command('find_placeholder_photos', '--clear', stdout=out)
        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertFalse(bad.image)
        self.assertTrue(good.image, 'a real photo must survive --clear')


class VisitBookingTests(TestCase):
    """Booking a time to come and see the gallery.

    Slots are shared on purpose: several visitors at the same half hour is fewer appointments for
    the gallery to keep, not a clash. That is what removes locking, held slots and double-booking
    races, and it is worth a test of its own because it looks like a bug otherwise.
    """

    def setUp(self):
        from gallery.models import OpeningHours, Site
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 10th St', city='Berkeley', state='CA', postal_code='94710',
            email='info@120710.art', timezone='America/Los_Angeles',
            visits_enabled=True, visit_slot_minutes=30, visit_lead_hours=2,
            visit_horizon_days=14)
        # Open every day, so a test does not depend on which weekday it runs.
        for weekday in range(7):
            OpeningHours.objects.create(site=self.site, weekday=weekday,
                                        start=datetime.time(13, 0), end=datetime.time(16, 0))
        mail.outbox.clear()

    def _first_slot(self):
        from gallery import visits as engine
        days = engine.available(self.site)
        self.assertTrue(days, 'no slots were offered')
        return days[0].slots[0].start

    def _book(self, when=None, **extra):
        data = {'when': (when or self._first_slot()).isoformat(),
                'name': 'Ana Vidal', 'email': 'ana@example.com', 'party_size': '2',
                'note': '', 'address': ''}
        data.update(extra)
        return self.client.post(reverse('book_visit'), data, follow=True)

    # --- Slots ---

    def test_slots_come_from_the_structured_hours(self):
        from gallery import visits as engine
        times = [slot.start.strftime('%H:%M')
                 for slot in engine.available(self.site)[0].slots]
        self.assertTrue(set(times) <= {'13:00', '13:30', '14:00', '14:30', '15:00', '15:30'},
                        times)

    def test_a_slot_that_would_run_past_closing_is_not_offered(self):
        """Offering 3:45 for a half-hour visit at a gallery that shuts at four is how somebody
        arrives to a locked door."""
        from gallery import visits as engine
        for day in engine.available(self.site):
            for slot in day.slots:
                self.assertLessEqual((slot.start + datetime.timedelta(minutes=30)).time(),
                                     datetime.time(16, 0))

    def test_nothing_inside_the_notice_period_is_offered(self):
        from gallery import visits as engine
        now = timezone.now()
        for day in engine.available(self.site, now=now):
            for slot in day.slots:
                self.assertGreaterEqual(slot.start, now + datetime.timedelta(hours=2))

    def test_a_closure_removes_its_days(self):
        from gallery import visits as engine
        from gallery.models import SiteClosure
        tz = __import__('zoneinfo').ZoneInfo('America/Los_Angeles')
        today = timezone.now().astimezone(tz).date()
        SiteClosure.objects.create(site=self.site, start_date=today,
                                   end_date=today + datetime.timedelta(days=30),
                                   note='Between shows')
        self.assertEqual(engine.available(self.site), [])

    def test_a_venue_with_booking_switched_off_offers_nothing(self):
        from gallery import visits as engine
        self.site.visits_enabled = False
        self.site.save(update_fields=['visits_enabled'])
        self.assertEqual(engine.available(self.site), [])
        self.assertEqual(self.client.get(reverse('book_visit')).status_code, 404)

    # --- The point of the design ---

    def test_several_visitors_may_book_the_same_slot(self):
        """Not a bug. Fewer appointments to keep, and no locking anywhere in the system."""
        from gallery.models import Visit
        slot = self._first_slot()
        self._book(slot)
        self._book(slot, name='Sam Ready', email='sam@example.com')
        self.assertEqual(Visit.objects.filter(when=slot).count(), 2)

    def test_a_capacity_closes_a_slot_once_it_is_reached(self):
        """Shared, but not unlimited — a school group of twelve is worth a ceiling."""
        from gallery import visits as engine
        self.site.visit_capacity = 3
        self.site.save(update_fields=['visit_capacity'])
        slot = self._first_slot()

        self._book(slot)                       # party of 2, one place left
        self.assertTrue(engine.is_bookable(self.site, slot, party_size=1))
        self.assertFalse(engine.is_bookable(self.site, slot, party_size=2))

        self._book(slot, name='Sam', email='s@example.com', party_size='1')
        self.assertFalse(engine.is_bookable(self.site, slot, party_size=1))
        remaining = [slot.start for day in engine.available(self.site)
                     for slot in day.slots]
        self.assertNotIn(slot, remaining)

    def test_a_stale_page_cannot_book_a_slot_that_has_gone(self):
        """The page may have been open an hour; the notice period alone will have moved."""
        from gallery.models import Visit
        gone = timezone.now() + datetime.timedelta(minutes=10)
        r = self._book(gone)
        self.assertContains(r, 'that time has just gone')
        self.assertEqual(Visit.objects.count(), 0)

    # --- What is on, and which hours to prefer ---

    def test_the_page_says_which_show_is_up(self):
        """Choosing between two afternoons is usually choosing between two shows."""
        show = Show.objects.create(
            name='Repetition and Repair', status=Show.STATUS_PUBLISHED,
            start=timezone.now().date() - datetime.timedelta(days=1),
            end=timezone.now().date() + datetime.timedelta(days=20))
        show.sites.add(self.site)
        page = self.client.get(reverse('book_visit')).content.decode()
        self.assertIn('Repetition and Repair', page)

    def test_days_between_shows_are_still_offered_and_say_so(self):
        """They may still want to come — that is why those days are shown, not hidden."""
        page = self.client.get(reverse('book_visit')).content.decode()
        self.assertIn('Between shows — for anything other than seeing a show', page)
        self.assertIn('slot', page)

    def test_a_draft_show_is_not_named_to_visitors(self):
        show = Show.objects.create(
            name='Secret Plans', status=Show.STATUS_DRAFT,
            start=timezone.now().date() - datetime.timedelta(days=1),
            end=timezone.now().date() + datetime.timedelta(days=20))
        show.sites.add(self.site)
        self.assertNotIn('Secret Plans',
                         self.client.get(reverse('book_visit')).content.decode())

    def test_drop_in_hours_are_encouraged_over_arranged_ones(self):
        """Public hours cost the gallery no special trip, so they are the ones to steer towards."""
        from gallery.models import OpeningHours
        OpeningHours.objects.all().delete()
        for weekday in range(7):
            OpeningHours.objects.create(site=self.site, weekday=weekday,
                                        start=datetime.time(13, 0), end=datetime.time(16, 0))
            OpeningHours.objects.create(site=self.site, weekday=weekday,
                                        start=datetime.time(18, 0), end=datetime.time(20, 0),
                                        by_appointment=True)

        from gallery import visits as engine
        from gallery.calendars import site_timezone
        # A fixed morning, because "the first available day" is not always a whole one: run
        # this between the lead time and the end of public hours and today has its evening
        # by-arrangement slots left but none of its afternoon ones, which is correct
        # behaviour and used to fail this test for a few hours every day.
        tz = site_timezone(self.site)
        morning = datetime.datetime.combine(
            timezone.now().astimezone(tz).date() + datetime.timedelta(days=1),
            datetime.time(9, 0), tzinfo=tz)
        day = engine.available(self.site, now=morning)[0]
        self.assertTrue(day.open_slots)
        self.assertTrue(day.appointment_slots)
        self.assertTrue(all(not s.by_appointment for s in day.open_slots))
        self.assertTrue(all(s.by_appointment for s in day.appointment_slots))

        page = self.client.get(reverse('book_visit')).content.decode()
        self.assertIn('open to everyone', page)
        self.assertIn('no need to book', page)
        self.assertIn('By arrangement', page)
        # The encouraging line names the drop-in hours only.
        self.assertIn('1–4 PM', page)

    def test_an_arranged_slot_can_still_be_booked(self):
        """Encouraged is not the same as required."""
        from gallery import visits as engine
        from gallery.models import OpeningHours, Visit
        OpeningHours.objects.all().delete()
        for weekday in range(7):
            OpeningHours.objects.create(site=self.site, weekday=weekday,
                                        start=datetime.time(18, 0), end=datetime.time(20, 0),
                                        by_appointment=True)
        day = engine.available(self.site)[0]
        self.assertEqual(day.open_slots, [])
        self._book(day.appointment_slots[0].start)
        self.assertEqual(Visit.objects.count(), 1)

    def test_the_arrival_note_reaches_the_visitor(self):
        """The confirmation is the message somebody has open while standing at the door."""
        self.site.arrival_note = 'Ring the bell when you arrive.'
        self.site.save(update_fields=['arrival_note'])
        response = self._book()

        self.assertContains(response, 'Ring the bell when you arrive.')
        visitor = next(m for m in mail.outbox if m.to == ['ana@example.com'])
        html = next(b for b, kind in visitor.alternatives if kind == 'text/html')
        self.assertIn('Ring the bell when you arrive.', html)

    def test_the_arrival_note_is_on_the_calendar_event_too(self):
        from gallery import visits as engine
        from gallery.models import Visit
        self.site.arrival_note = 'Ring the bell when you arrive.'
        self.site.save(update_fields=['arrival_note'])
        self._book()
        ics = engine.invitation(Visit.objects.get())
        self.assertIn('Ring the bell', ics)

    def test_a_venue_with_no_arrival_note_says_nothing(self):
        response = self._book()
        self.assertNotContains(response, 'Ring the bell')

    def test_the_arrival_note_is_editable(self):
        from gallery.forms import SiteForm
        self.assertIn('arrival_note', SiteForm().fields)

    def test_booking_is_the_only_way_offered_to_arrange_a_visit(self):
        """No email back door: a visit arranged by email is not in the calendar, so the calendar
        stops being the record of who is coming."""
        self.site.phone = '341-205-1331'
        self.site.save(update_fields=['phone'])
        response = self._book()

        visitor = next(m for m in mail.outbox if m.to == ['ana@example.com'])
        html = next(b for b, kind in visitor.alternatives if kind == 'text/html')
        self.assertNotIn('info@120710.art', html)
        self.assertNotIn('mailto:', html)
        self.assertNotIn('341-205-1331', html)
        # Changing plans goes through the cancellation link, which updates the calendar.
        self.assertIn('/visit/cancel/', html)

    def test_the_empty_state_does_not_offer_email_instead(self):
        from gallery.models import OpeningHours
        OpeningHours.objects.all().delete()
        page = self.client.get(reverse('book_visit')).content.decode()
        self.assertIn('do check back', page)
        # The page's own content, not the base template — which carries a commented-out footer
        # with a stale address in it, invisible to a reader and visible to a grep.
        body = page.split('id="page-title"')[1].split('<footer')[0]
        self.assertNotIn('info@120710.art', body)
        self.assertNotIn('mailto:', body)

    def test_the_visit_page_stops_offering_email_once_booking_is_on(self):
        """"by calling … or emailing …" is how you arrange a visit somewhere with no booking
        page. Where there is one, it is a second way in that never reaches the calendar."""
        self.site.email = 'info@120710.art'
        self.site.phone = '341-205-1331'
        self.site.save(update_fields=['email', 'phone'])

        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            page = self.client.get(reverse('visit')).content.decode()
        body = page.split('id="page-title"')[1].split('<footer')[0]
        self.assertIn('Book a time to visit', body)
        self.assertNotIn('or emailing', body)
        self.assertNotIn('by calling', body)
        self.assertNotIn('info@120710.art', body)

    def test_no_public_page_hands_out_an_address_or_a_number(self):
        """The gallery is reached by booking, by the mailing list, or by an enquiry on a work.

        Publishing an address also hands it to every scraper that reads the page. Site.email and
        Site.phone still exist and are still used — the address is where visit notifications go
        and who campaigns come from — they are simply not printed.
        """
        self.site.email = 'info@120710.art'
        self.site.phone = '341-205-1331'
        self.site.save(update_fields=['email', 'phone'])

        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            for name in ('visit', 'contact'):
                with self.subTest(page=name):
                    page = self.client.get(reverse(name)).content.decode()
                    body = page.split('id="page-title"')[1].split('<footer')[0]
                    self.assertNotIn('info@120710.art', body)
                    self.assertNotIn('341-205-1331', body)
                    self.assertNotIn('mailto:', body)
                    self.assertNotIn('tel:', body)

    def test_the_confirmation_email_hands_out_neither_either(self):
        self.site.email = 'info@120710.art'
        self.site.phone = '341-205-1331'
        self.site.save(update_fields=['email', 'phone'])
        self._book()
        visitor = next(m for m in mail.outbox if m.to == ['ana@example.com'])
        html = next(b for b, kind in visitor.alternatives if kind == 'text/html')
        self.assertNotIn('341-205-1331', html)
        self.assertNotIn('mailto:', html)
        # The cancellation link is the way to change plans, and it updates the calendar.
        self.assertIn('/visit/cancel/', html)

    def test_a_campaign_footer_hands_out_neither(self):
        from gallery.models import Campaign
        self.site.email = 'info@120710.art'
        self.site.phone = '341-205-1331'
        self.site.save(update_fields=['email', 'phone'])
        campaign = Campaign.objects.create(
            site=self.site, subject='Hello', body_markdown='Body.')
        html = campaigns.render_preview(campaign)
        self.assertNotIn('341-205-1331', html)
        self.assertNotIn('To arrange a time', html)
        self.assertIn('Book a time to visit', html)

    def test_the_contact_page_links_to_booking_from_the_hours(self):
        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            page = self.client.get(reverse('contact')).content.decode()
        self.assertIn('Book a time to visit', page)

    def test_a_campaign_points_at_the_booking_page_rather_than_an_address(self):
        from gallery.models import Campaign
        campaign = Campaign.objects.create(
            site=self.site, subject='Hello', body_markdown='Body.')
        html = campaigns.render_preview(campaign)
        self.assertIn('Book a time to visit', html)
        self.assertNotIn('To arrange a time', html)

    # --- Signed in ---

    def test_a_signed_in_visitor_is_not_asked_who_they_are(self):
        from gallery.models import Visit
        user = User.objects.create_user(username='ana@example.com', email='ana@example.com',
                                        password='pw', first_name='Ana', last_name='Vidal')
        self.client.force_login(user)

        page = self.client.get(reverse('book_visit')).content.decode()
        self.assertIn('Booking as', page)
        self.assertNotIn('id_email', page)

        self.client.post(reverse('book_visit'), {
            'when': self._first_slot().isoformat(), 'party_size': '2',
            'note': '', 'address': ''}, follow=True)
        visit = Visit.objects.get()
        self.assertEqual(visit.name, 'Ana Vidal')
        self.assertEqual(visit.email, 'ana@example.com')

    def test_a_signed_in_visitor_cannot_book_under_another_name(self):
        """Taken from the account, never from the post, or a hidden field would be enough."""
        from gallery.models import Visit
        user = User.objects.create_user(username='ana@example.com', email='ana@example.com',
                                        password='pw', first_name='Ana', last_name='Vidal')
        self.client.force_login(user)
        self.client.post(reverse('book_visit'), {
            'when': self._first_slot().isoformat(), 'party_size': '1',
            'name': 'Someone Else', 'email': 'someone@example.com',
            'note': '', 'address': ''}, follow=True)
        visit = Visit.objects.get()
        self.assertEqual(visit.name, 'Ana Vidal')
        self.assertEqual(visit.email, 'ana@example.com')

    def test_a_signed_out_visitor_is_still_asked(self):
        page = self.client.get(reverse('book_visit')).content.decode()
        self.assertIn('Your name', page)
        self.assertNotIn('Booking as', page)

    # --- The calendar feed ---

    def _feed_url(self):
        self.site.refresh_from_db()
        if not self.site.visit_feed_token:
            self.site.save(update_fields=['visit_feed_token'])
            self.site.refresh_from_db()
        return f'/visits/{self.site.visit_feed_token}.ics'

    def test_the_feed_says_there_is_a_visit_without_saying_who(self):
        """A calendar entry travels further than an inbox does — a phone on a table, a screen
        shared in a meeting, the feed itself. Knowing a visit is booked does not require knowing
        whose it is, so the name and address sit one authenticated click away instead."""
        self._book()
        body = self.client.get(self._feed_url()).content.decode()
        self.assertIn('BEGIN:VEVENT', body)
        self.assertIn('120710 — visits', body)
        self.assertIn('Visit — 2 people', body)

        self.assertNotIn('Ana Vidal', body)
        self.assertNotIn('ana@example.com', body)

        from gallery.models import Visit
        self.assertIn(reverse('gallery:visit_detail',
                              kwargs={'pk': Visit.objects.get().pk}), body)

    def test_the_reveal_page_needs_a_login(self):
        from gallery.models import Visit
        self._book()
        url = reverse('gallery:visit_detail', kwargs={'pk': Visit.objects.get().pk})
        self.assertEqual(self.client.get(url).status_code, 302)

        artist = User.objects.create_user(username='nv@example.com', email='nv@example.com',
                                          password='pw')
        self.client.force_login(artist)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_the_reveal_page_shows_who_it_is(self):
        from gallery.models import Visit
        self._book()
        staff = User.objects.create_user(username='rv@example.com', email='rv@example.com',
                                         password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        page = self.client.get(reverse('gallery:visit_detail',
                                       kwargs={'pk': Visit.objects.get().pk}))
        self.assertContains(page, 'Ana Vidal')
        self.assertContains(page, 'ana@example.com')

    def test_a_cancelled_visit_simply_leaves_the_feed(self):
        """The whole behaviour a subscribed feed offers, and why no SEQUENCE dance is needed."""
        from gallery import visits as engine
        from gallery.models import Visit
        self._book()
        visit = Visit.objects.get()
        self.client.post(reverse('visit_cancel',
                                 kwargs={'token': engine.cancel_token(visit)}))
        body = self.client.get(self._feed_url()).content.decode()
        self.assertNotIn('Ana Vidal', body)

    def test_the_feed_needs_its_secret(self):
        """It carries names and email addresses, and a subscribed calendar cannot sign in — so
        the URL is the credential."""
        self._book()
        self.assertEqual(self.client.get('/visits/not-the-token.ics').status_code, 404)

    def test_a_wrong_token_does_not_confirm_the_feed_exists(self):
        self.assertEqual(self.client.get('/visits/wrong.ics').status_code, 404)

    def test_the_feed_is_not_cached_by_anything_shared_or_indexed(self):
        self._book()
        response = self.client.get(self._feed_url())
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('noindex', response['X-Robots-Tag'])

    def test_the_address_can_be_changed_if_it_gets_out(self):
        self._book()
        old_url = self._feed_url()
        staff = User.objects.create_user(username='fd@example.com', email='fd@example.com',
                                         password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        self.client.post(reverse('gallery:regenerate_visit_feed',
                                 kwargs={'pk': self.site.pk}))

        self.assertEqual(self.client.get(old_url).status_code, 404)
        self.assertEqual(self.client.get(self._feed_url()).status_code, 200)

    def test_only_staff_can_change_the_address(self):
        url = reverse('gallery:regenerate_visit_feed', kwargs={'pk': self.site.pk})
        self.assertEqual(self.client.post(url).status_code, 302)
        artist = User.objects.create_user(username='na@example.com', email='na@example.com',
                                          password='pw')
        self.client.force_login(artist)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_staff_are_shown_the_address_and_warned_about_it(self):
        staff = User.objects.create_user(username='fs@example.com', email='fs@example.com',
                                         password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        page = self.client.get(reverse('gallery:visit_list'))
        self.assertContains(page, '.ics')
        self.assertContains(page, 'worth keeping to yourself')

    # --- Timezones ---

    def test_slots_are_shown_in_the_venues_own_time(self):
        """TIME_ZONE is UTC and USE_TZ is on, so Django converts every aware datetime to UTC for
        display unless told otherwise — which turned a noon opening in Berkeley into a 7pm slot
        on the page. The datetimes were right; only the rendering was wrong.
        """
        from gallery.models import OpeningHours
        OpeningHours.objects.all().delete()
        for weekday in range(7):
            OpeningHours.objects.create(site=self.site, weekday=weekday,
                                        start=datetime.time(12, 0), end=datetime.time(17, 0))

        page = self.client.get(reverse('book_visit')).content.decode()
        self.assertIn('12:00 PM', page)
        # 12:00 in Berkeley is 19:00 UTC. Seeing that on the page is the bug.
        self.assertNotIn('7:00 PM', page)
        self.assertNotIn('11:30 PM', page)
        self.assertIn('America/Los_Angeles', page)

    def test_the_confirmation_page_and_emails_agree_with_the_slot(self):
        """A visitor told one time on the page and another by email arrives at neither."""
        from gallery.models import OpeningHours
        OpeningHours.objects.all().delete()
        for weekday in range(7):
            OpeningHours.objects.create(site=self.site, weekday=weekday,
                                        start=datetime.time(12, 0), end=datetime.time(17, 0))
        slot = self._first_slot()
        shown = slot.strftime('%-I:%M %p')

        response = self._book(slot)
        self.assertContains(response, shown)
        visitor = next(m for m in mail.outbox if m.to == ['ana@example.com'])
        html = next(b for b, kind in visitor.alternatives if kind == 'text/html')
        self.assertIn(shown, html)
        gallery = next(m for m in mail.outbox if m.to == ['info@120710.art'])
        gallery_html = next(b for b, kind in gallery.alternatives if kind == 'text/html')
        self.assertIn(shown, gallery_html)

    def test_the_calendar_invitation_carries_the_right_instant(self):
        """The .ics is in UTC by design — that part was never wrong, and must stay right."""
        from gallery import visits as engine
        from gallery.models import Visit
        self._book()
        visit = Visit.objects.get()
        ics = engine.invitation(visit)
        expected = visit.when.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.assertIn(f'DTSTART:{expected}', ics)

    def test_the_staff_list_shows_each_venues_own_time(self):
        """It spans venues, so activating one zone would not do — each row is pre-converted."""
        from gallery.models import OpeningHours
        OpeningHours.objects.all().delete()
        for weekday in range(7):
            OpeningHours.objects.create(site=self.site, weekday=weekday,
                                        start=datetime.time(12, 0), end=datetime.time(17, 0))
        slot = self._first_slot()
        self._book(slot)

        staff = User.objects.create_user(username='tz@example.com', email='tz@example.com',
                                         password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        page = self.client.get(reverse('gallery:visit_list')).content.decode()
        self.assertIn(slot.strftime('%-I:%M %p'), page)

    # --- Emails ---

    def test_booking_emails_the_visitor_and_the_gallery(self):
        self._book()
        self.assertEqual(len(mail.outbox), 2)
        to = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(to, ['ana@example.com', 'info@120710.art'])

    def test_the_gallery_gets_a_calendar_invitation_not_an_attachment(self):
        """METHOD:REQUEST is what makes Google Calendar add it by itself, with no integration."""
        self._book()
        gallery = next(m for m in mail.outbox if m.to == ['info@120710.art'])
        kinds = [kind for _, kind in gallery.alternatives]
        calendar = next(c for c in kinds if c.startswith('text/calendar'))
        self.assertIn('method=REQUEST', calendar)
        body = next(b for b, kind in gallery.alternatives if kind.startswith('text/calendar'))
        self.assertIn('METHOD:REQUEST', body)
        self.assertIn('BEGIN:VEVENT', body)
        # The event itself names nobody; the email around it does.
        self.assertNotIn('Ana Vidal', body)
        self.assertIn('Visit — 2 people', body)
        self.assertNotIn('ana@example.com', body)
        # The gallery is the attendee, which is what makes a client add it — the message is
        # addressed to them. The email around the invitation is where the name is.
        self.assertIn('ATTENDEE', body)
        self.assertIn('info@120710.art', body)
        html = next(b for b, kind in gallery.alternatives if kind == 'text/html')
        self.assertIn('Ana Vidal', html)

    def test_the_visitor_gets_their_copy_and_a_way_out(self):
        self._book()
        visitor = next(m for m in mail.outbox if m.to == ['ana@example.com'])
        html = next(b for b, kind in visitor.alternatives if kind == 'text/html')
        self.assertIn('/visit/cancel/', html)
        # Not an invitation to respond to — they made the booking.
        calendar = next(kind for _, kind in visitor.alternatives
                        if kind.startswith('text/calendar'))
        self.assertIn('method=PUBLISH', calendar)

    def test_a_mail_failure_does_not_lose_the_booking(self):
        """The visitor has been told it worked, so it has to have worked."""
        from unittest import mock
        from gallery.models import Visit
        with mock.patch('gallery.visits.EmailMultiAlternatives.send',
                        side_effect=RuntimeError('mail down')):
            self._book()
        self.assertEqual(Visit.objects.count(), 1)

    # --- Cancelling ---

    def test_a_visitor_can_cancel_from_their_email(self):
        from gallery import visits as engine
        from gallery.models import Visit
        self._book()
        visit = Visit.objects.get()
        url = reverse('visit_cancel', kwargs={'token': engine.cancel_token(visit)})

        # A GET only asks — mail clients prefetch links, and a scanner must not cancel a visit.
        self.client.get(url)
        self.assertFalse(Visit.objects.get().is_cancelled)

        mail.outbox.clear()
        self.client.post(url)
        visit = Visit.objects.get()
        self.assertTrue(visit.is_cancelled)
        self.assertEqual(len(mail.outbox), 1)
        body = next(b for b, kind in mail.outbox[0].alternatives
                    if kind.startswith('text/calendar'))
        self.assertIn('METHOD:CANCEL', body)

    def test_a_cancellation_advances_the_sequence(self):
        """A calendar client ignores an update whose SEQUENCE has not moved, so a cancellation
        at the same sequence is dropped and the appointment stays for good."""
        from gallery import visits as engine
        from gallery.models import Visit
        self._book()
        visit = Visit.objects.get()
        self.assertEqual(visit.sequence, 0)
        self.client.post(reverse('visit_cancel',
                                 kwargs={'token': engine.cancel_token(visit)}))
        self.assertEqual(Visit.objects.get().sequence, 1)

    def test_the_invitation_and_its_cancellation_share_a_uid(self):
        """Different UIDs would leave the original sitting in the calendar."""
        from gallery import visits as engine
        from gallery.models import Visit
        self._book()
        visit = Visit.objects.get()
        request = engine.invitation(visit, method='REQUEST')
        cancel = engine.invitation(visit, method='CANCEL')
        self.assertIn(visit.uid(), request)
        self.assertIn(visit.uid(), cancel)

    def test_a_tampered_cancellation_link_does_nothing(self):
        from gallery.models import Visit
        self._book()
        r = self.client.post(reverse('visit_cancel', kwargs={'token': 'not-a-real-token'}))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Visit.objects.get().is_cancelled)

    def test_a_cancelled_slot_frees_its_place_again(self):
        from gallery import visits as engine
        from gallery.models import Visit
        self.site.visit_capacity = 2
        self.site.save(update_fields=['visit_capacity'])
        slot = self._first_slot()
        self._book(slot)                       # party of 2 fills it
        self.assertFalse(engine.is_bookable(self.site, slot, party_size=1))

        visit = Visit.objects.get()
        self.client.post(reverse('visit_cancel',
                                 kwargs={'token': engine.cancel_token(visit)}))
        self.assertTrue(engine.is_bookable(self.site, slot, party_size=1))

    # --- Staff ---

    def test_staff_can_see_who_is_coming(self):
        self._book()
        staff = User.objects.create_user(username='v@example.com', email='v@example.com',
                                         password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        page = self.client.get(reverse('gallery:visit_list'))
        self.assertContains(page, 'Ana Vidal')
        self.assertContains(page, 'ana@example.com')

    def test_the_visit_list_is_not_public(self):
        self._book()
        self.assertEqual(self.client.get(reverse('gallery:visit_list')).status_code, 302)
        artist = User.objects.create_user(username='a2@example.com', email='a2@example.com',
                                          password='pw')
        self.client.force_login(artist)
        self.assertEqual(self.client.get(reverse('gallery:visit_list')).status_code, 404)


class CampaignOutcomeTests(TestCase):
    """What happened after the send, not just what we did.

    Before this the pages could report that a campaign went to 412 people and nothing else — a
    bounce arriving ten minutes later unsubscribed the person and left no mark on the campaign.
    Bounce rate is how a stale imported list announces itself, and complaint rate is the number
    Gmail and Yahoo actually judge a sender on, so neither being visible was the gap.
    """

    def setUp(self):
        from gallery.models import Campaign, Site, Subscriber, Subscription
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 10th St', city='Berkeley', state='CA', postal_code='94710')
        for i in range(4):
            Subscriber.opt_in(email='o%d@example.com' % i, sites=[self.site],
                              source=Subscription.SOURCE_SUBSCRIBE_FORM)
        self.campaign = Campaign.objects.create(
            site=self.site, subject='Opening night', body_markdown='Hello.')
        self.campaign.test_sent_at = timezone.now()
        self.campaign.save(update_fields=['test_sent_at'])
        self.staff = User.objects.create_user(
            username='out@example.com', email='out@example.com', password='pw')
        add_staff_role(self.staff)
        self.client.force_login(self.staff)
        mail.outbox.clear()

    def _send(self):
        from unittest import mock
        with mock.patch('gallery.campaigns._connection',
                        lambda: mail.get_connection(
                            'django.core.mail.backends.locmem.EmailBackend')):
            campaigns.send_campaign(self.campaign)
        self.campaign.refresh_from_db()

    def _event(self, kind, address):
        from anymail.signals import AnymailTrackingEvent, EventType, tracking
        tracking.send(sender=object(), esp_name='Resend',
                      event=AnymailTrackingEvent(
                          event_type=getattr(EventType, kind), recipient=address))

    def test_a_bounce_is_counted_against_the_campaign_that_caused_it(self):
        self._send()
        self.assertEqual(self.campaign.sent_so_far, 4)

        self._event('BOUNCED', 'o0@example.com')
        self.assertEqual(self.campaign.bounced_count, 1)
        self.assertEqual(self.campaign.bounce_rate, 25.0)
        # And the send count is untouched: what we did does not change because of what followed.
        self.assertEqual(self.campaign.sent_so_far, 4)

    def test_a_complaint_is_counted_and_its_rate_reported(self):
        self._send()
        self._event('COMPLAINED', 'o0@example.com')
        self.assertEqual(self.campaign.complained_count, 1)
        self.assertEqual(self.campaign.complaint_rate, 25.0)
        self.assertTrue(self.campaign.complaint_rate_is_high)

    def test_a_healthy_campaign_is_not_flagged(self):
        self._send()
        self.assertEqual(self.campaign.complaint_rate, 0.0)
        self.assertFalse(self.campaign.complaint_rate_is_high)

    def test_the_person_is_still_unsubscribed_everywhere(self):
        """Attribution is an addition; the thing that protects the domain must still happen."""
        from gallery.models import Subscriber
        self._send()
        self._event('BOUNCED', 'o0@example.com')
        subscription = Subscriber.objects.get(email='o0@example.com').subscriptions.get()
        self.assertFalse(subscription.is_subscribed)
        self.assertEqual(subscription.unsubscribed_reason, 'bounced')

    def test_a_retried_webhook_cannot_count_one_complaint_twice(self):
        """Providers retry. Double-counting would double a campaign's rate."""
        self._send()
        for _ in range(3):
            self._event('COMPLAINED', 'o0@example.com')
        self.assertEqual(self.campaign.complained_count, 1)

    def test_an_event_for_a_long_past_send_is_not_blamed_on_a_recent_campaign(self):
        """People press the spam button on months-old mail.

        Attributing that to whatever went out last week would blame a campaign that had nothing
        to do with it and inflate its complaint rate.
        """
        from gallery.models import CampaignDelivery
        self._send()
        CampaignDelivery.objects.update(
            sent_at=timezone.now() - datetime.timedelta(days=60))

        self._event('COMPLAINED', 'o0@example.com')
        self.assertEqual(self.campaign.complained_count, 0)
        # But they are still taken off the list, which is the part that matters.
        from gallery.models import Subscriber
        self.assertFalse(
            Subscriber.objects.get(email='o0@example.com').subscriptions.get().is_subscribed)

    def test_an_event_for_someone_who_was_never_mailed_records_nothing(self):
        from gallery.models import Subscriber, Subscription
        self._send()
        Subscriber.opt_in(email='later@example.com', sites=[self.site],
                          source=Subscription.SOURCE_SUBSCRIBE_FORM)
        self._event('BOUNCED', 'later@example.com')
        self.assertEqual(self.campaign.bounced_count, 0)

    def test_the_campaign_page_reports_what_happened_after(self):
        self._send()
        self._event('BOUNCED', 'o0@example.com')
        self._event('COMPLAINED', 'o1@example.com')

        page = self.client.get(reverse('gallery:campaign_edit',
                                       kwargs={'pk': self.campaign.pk}))
        self.assertContains(page, 'After the send')
        self.assertContains(page, 'Marked as spam')
        self.assertContains(page, '25.0%')
        # Literal template text is not escaped — only variables are.
        self.assertContains(page, "start putting a domain's mail in spam folders")

    def test_the_list_shows_the_rates_without_a_query_per_row(self):
        self._send()
        self._event('BOUNCED', 'o0@example.com')
        with self.assertNumQueries(6):
            page = self.client.get(reverse('gallery:campaign_list'))
        self.assertContains(page, 'Bounced')
        self.assertContains(page, '25.0%')


class CampaignResumeTests(TestCase):
    """A send that stops part-way must be finishable without mailing anyone twice.

    This is the failure that would actually hurt. Before delivery records, a send that died
    at message 400 of 900 left no trace of how far it got: the campaign could not be sent
    again, and re-creating it would have mailed the first 400 people a second time. These
    tests pin the two properties that make it recoverable — every send skips people with a
    delivery record, and a stopped send is visible as stopped.
    """

    def setUp(self):
        from gallery.models import Campaign, Site, Subscriber, Subscription
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 Tenth Street', city='Berkeley', state='CA', postal_code='94710')
        for i in range(5):
            Subscriber.opt_in(email='r%d@example.com' % i, first_name='Reader',
                              last_name=str(i), sites=[self.site],
                              source=Subscription.SOURCE_SUBSCRIBE_FORM)
        self.campaign = Campaign.objects.create(
            site=self.site, subject='Opening night', body_markdown='Come **along**.')
        # After creation, not in it: edited_at is auto_now_add, so a test timestamp passed to
        # create() predates it and the send guard would refuse every one of these tests.
        self.campaign.test_sent_at = timezone.now()
        self.campaign.save(update_fields=['test_sent_at'])
        self.staff = User.objects.create_user(
            username='res@example.com', email='res@example.com', password='pw')
        add_staff_role(self.staff)
        self.client.force_login(self.staff)
        # The outbox is process-wide. Django empties it between tests, but signals firing
        # during this setUp can put mail in it, and these tests assert on exactly who was
        # mailed — so start from empty.
        mail.outbox.clear()

    def _batches_of_two(self):
        """Two per batch, so a five-person list takes three batches and can fail between."""
        from unittest import mock
        return mock.patch('gallery.campaigns.BATCH_SIZE', 2)

    def _dies_after(self, messages):
        """A connection that delivers `messages` messages and then becomes unreachable.

        Models the provider going away mid-campaign — an outage, a network drop, a rate limit
        that never clears. Deliberately a *transient* failure, because that is what a resume
        exists for: nobody is at fault, the addresses are still owed the mailing, and they must
        be left pending rather than written off.

        Per message rather than per batch, because that is what the backend really does: one
        API call each, which is exactly why every delivery can be recorded individually and no
        resume ever repeats one.
        """
        import contextlib
        from unittest import mock

        outer = self

        class Dying:
            done = 0

            def open(self):
                pass

            def close(self):
                pass

            def send_messages(self, batch):
                if Dying.done >= messages:
                    error = Exception('provider went away')
                    error.status_code = 503
                    raise error
                Dying.done += len(batch)
                outer.delivered.extend(m.to[0] for m in batch)
                return len(batch)

        self.delivered = []

        @contextlib.contextmanager
        def patched():
            with mock.patch('gallery.campaigns._connection', lambda: Dying()), \
                    mock.patch('gallery.campaigns.RETRY_BACKOFF', 0):
                yield

        return patched()

    def _locmem(self):
        from unittest import mock
        return mock.patch('gallery.campaigns._connection',
                          lambda: mail.get_connection(
                              'django.core.mail.backends.locmem.EmailBackend'))

    def _sent_to(self):
        """The addresses this send reached.

        A set of addresses rather than a count of the outbox: the outbox is process-wide, and
        asserting on its length made these tests fail intermittently depending on which other
        test ran before them in the same worker. What matters here is who got the campaign.
        """
        return {m.to[0] for m in mail.outbox}

    # --- The engine ---

    def test_a_send_that_dies_part_way_records_what_it_delivered(self):
        with self._batches_of_two(), self._dies_after(4):
            self.assertEqual(campaigns.send_campaign(self.campaign), 4)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'failed')
        # Four went out and are recorded individually; the fifth never did.
        self.assertEqual(self.campaign.sent_so_far, 4)
        self.assertEqual(self.campaign.remaining_count, 1)
        self.assertTrue(self.campaign.can_resume)

    def test_resuming_mails_only_the_people_who_missed_it(self):
        with self._batches_of_two(), self._dies_after(4):
            campaigns.send_campaign(self.campaign)
        already = set(self.delivered)
        self.assertEqual(len(already), 4)

        self.campaign.refresh_from_db()
        with self._locmem():
            sent = campaigns.send_campaign(self.campaign, resume=True)

        self.assertEqual(sent, 1, 'a resume must not re-send to the four already reached')
        self.assertEqual([m.to[0] for m in mail.outbox],
                         [e for e in ['r0@example.com', 'r1@example.com', 'r2@example.com',
                                      'r3@example.com', 'r4@example.com']
                          if e not in already])

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertEqual(self.campaign.recipient_count, 5)
        self.assertEqual(self.campaign.remaining_count, 0)

    def test_nobody_is_mailed_twice_even_if_resume_is_pressed_repeatedly(self):
        with self._batches_of_two(), self._dies_after(2):
            campaigns.send_campaign(self.campaign)

        for _ in range(3):
            self.campaign.refresh_from_db()
            if not self.campaign.remaining_count:
                break
            with self._locmem():
                campaigns.send_campaign(self.campaign, resume=True)

        addresses = [m.to[0] for m in mail.outbox] + self.delivered
        self.assertEqual(len(addresses), 5)
        self.assertEqual(len(set(addresses)), 5, 'somebody received it twice')

    def test_someone_who_unsubscribes_after_the_failure_is_not_mailed_by_the_resume(self):
        """The list is re-read on resume, not frozen at the moment the send started.

        Sending to somebody who opted out in between is the one thing worse than not
        finishing at all.
        """
        from gallery.models import Subscription
        with self._batches_of_two(), self._dies_after(2):
            campaigns.send_campaign(self.campaign)

        missed = campaigns.pending(self.campaign).first()
        gone = missed.subscriber.email
        missed.unsubscribe(reason=Subscription.UNSUB_REQUESTED)

        self.campaign.refresh_from_db()
        with self._locmem():
            campaigns.send_campaign(self.campaign, resume=True)

        self.assertNotIn(gone, [m.to[0] for m in mail.outbox])

    def test_a_send_abandoned_by_a_dead_process_becomes_resumable(self):
        """Nothing can report from inside a process that has been killed.

        A deploy mid-send leaves the row saying `sending` forever, which looks like work in
        progress rather than a problem. Absence of progress is the only available signal.
        """
        from gallery.models import Campaign
        Campaign.objects.filter(pk=self.campaign.pk).update(
            status=Campaign.STATUS_SENDING,
            progress_at=timezone.now() - datetime.timedelta(minutes=11))
        self.campaign.refresh_from_db()

        self.assertTrue(self.campaign.is_stalled)
        self.assertTrue(self.campaign.can_resume)
        self.assertIn('stopped after', self.campaign.blocked_reason)

        with self._locmem():
            campaigns.send_campaign(self.campaign, resume=True)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertEqual(self._sent_to(), {'r0@example.com', 'r1@example.com',
                                           'r2@example.com', 'r3@example.com',
                                           'r4@example.com'})

    def test_a_running_send_keeps_saying_it_is_alive(self):
        """Otherwise a slow send looks abandoned and can be resumed alongside itself.

        The progress clock is what `is_stalled` reads, so it has to move on elapsed time rather
        than message count — a throttled send can spend longer than STALL_AFTER inside a single
        batch.
        """
        from unittest import mock
        with self._locmem(), mock.patch('gallery.campaigns.PROGRESS_EVERY_SECONDS', 0):
            campaigns.send_campaign(self.campaign)
        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.progress_at)
        self.assertFalse(self.campaign.is_stalled)

    def test_a_send_still_making_progress_is_not_treated_as_stopped(self):
        from gallery.models import Campaign
        Campaign.objects.filter(pk=self.campaign.pk).update(
            status=Campaign.STATUS_SENDING, progress_at=timezone.now())
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_stalled)
        self.assertFalse(self.campaign.can_resume)
        with self.assertRaises(ValueError):
            campaigns.send_campaign(self.campaign, resume=True)

    def test_two_sends_at_once_cannot_both_claim_the_campaign(self):
        """A double-clicked button, or two workers behind one URL.

        The unique constraint on the delivery records would catch the duplicate, but only
        after the mail had gone out. The claim has to happen first.
        """
        from gallery.models import Campaign
        stale = Campaign.objects.get(pk=self.campaign.pk)

        with self._locmem():
            campaigns.send_campaign(self.campaign)
        self.assertEqual(len(mail.outbox), 5)

        # The second caller is holding the row as it was before the first one claimed it.
        mail.outbox.clear()
        with self._locmem():
            with self.assertRaises(ValueError):
                campaigns.send_campaign(stale)
        self.assertEqual(mail.outbox, [])

    def test_resume_is_refused_on_a_campaign_that_never_stopped(self):
        with self.assertRaises(ValueError):
            campaigns.send_campaign(self.campaign, resume=True)

    def test_deleting_a_subscriber_takes_their_delivery_record_with_them(self):
        """Erasure means erasure, including the record of having mailed them."""
        from gallery.models import CampaignDelivery, Subscriber
        with self._locmem():
            campaigns.send_campaign(self.campaign)
        self.assertEqual(CampaignDelivery.objects.count(), 5)

        Subscriber.objects.get(email='r0@example.com').delete()
        self.assertEqual(CampaignDelivery.objects.count(), 4)

    # --- The page ---

    def test_the_page_offers_resume_and_says_how_far_it_got(self):
        with self._batches_of_two(), self._dies_after(4):
            campaigns.send_campaign(self.campaign)

        page = self.client.get(reverse('gallery:campaign_edit',
                                       kwargs={'pk': self.campaign.pk}))
        self.assertContains(page, 'This send stopped before it finished')
        self.assertContains(page, '4 of 5 subscriber')
        self.assertContains(page, 'nobody who already got it will get it twice')
        self.assertContains(page, reverse('gallery:campaign_resume',
                                          kwargs={'pk': self.campaign.pk}))
        # And not the banner that describes a finished send as a record.
        self.assertNotContains(page, 'it is the record of what went out')

    def test_resuming_from_the_page_finishes_the_send(self):
        with self._batches_of_two(), self._dies_after(4):
            campaigns.send_campaign(self.campaign)

        with self._locmem():
            r = self.client.post(reverse('gallery:campaign_resume',
                                         kwargs={'pk': self.campaign.pk}), follow=True)
        self.assertContains(r, 'Resuming to 1 subscriber')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertEqual(len(mail.outbox), 1)

    def test_resume_from_the_page_is_refused_when_there_is_nothing_to_finish(self):
        r = self.client.post(reverse('gallery:campaign_resume',
                                     kwargs={'pk': self.campaign.pk}), follow=True)
        self.assertContains(r, 'nothing to resume')
        self.assertEqual(mail.outbox, [])

    def test_only_staff_can_resume(self):
        url = reverse('gallery:campaign_resume', kwargs={'pk': self.campaign.pk})
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        artist = User.objects.create_user(
            username='no@example.com', email='no@example.com', password='pw')
        self.client.force_login(artist)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(mail.outbox, [])

    # --- Rate limits and a provider having a bad day ---

    def test_a_rate_limit_is_retried_rather_than_failing_the_campaign(self):
        """The most likely failure on a real list, and it must not need a human.

        The backend makes one API request per message, so a thousand-person send is a thousand
        requests against a two-a-second allowance. A 429 is ordinary weather; stopping the
        campaign and waiting for somebody to press Resume would be the wrong response to it.
        """
        from unittest import mock

        class RateLimited:
            attempts = 0

            def open(self):
                pass

            def close(self):
                pass

            def send_messages(self, batch):
                RateLimited.attempts += 1
                if RateLimited.attempts == 2:
                    error = Exception('Too many requests')
                    error.status_code = 429
                    raise error
                return len(batch)

        with mock.patch('gallery.campaigns._connection', lambda: RateLimited()), \
                mock.patch('gallery.campaigns.RETRY_BACKOFF', 0):
            sent = campaigns.send_campaign(self.campaign)

        self.assertEqual(sent, 5, 'the rate-limited message should have been retried')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertEqual(self.campaign.sent_so_far, 5)

    def test_a_refused_address_is_unsubscribed_and_does_not_hold_the_campaign_open(self):
        """The common case, and it must not need any cleaning up by hand.

        A provider refusing an address outright is a hard bounce told to us up front rather than
        by webhook ten minutes later, so it gets the same treatment: stop mailing them. The
        campaign finishes, because there is nothing a resume could usefully do about an address
        that will never accept mail — pressing Resume forever was the old behaviour and it was
        no behaviour at all.
        """
        from unittest import mock
        from gallery.models import CampaignDelivery, Subscriber

        class Picky:
            calls = 0

            def open(self):
                pass

            def close(self):
                pass

            def send_messages(self, batch):
                Picky.calls += 1
                if batch[0].to[0] == 'r2@example.com':
                    error = Exception('Invalid `to` field: mailbox does not exist')
                    error.status_code = 422
                    raise error
                return len(batch)

        with mock.patch('gallery.campaigns._connection', lambda: Picky()), \
                mock.patch('gallery.campaigns.RETRY_BACKOFF', 0):
            sent = campaigns.send_campaign(self.campaign)

        self.assertEqual(sent, 4)
        self.assertEqual(Picky.calls, 5, 'a 422 about the address must not be retried')

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent',
                         'four of five delivered and the fifth refused is a finished campaign')
        self.assertEqual(self.campaign.sent_so_far, 4)
        self.assertEqual(self.campaign.recipient_count, 4)

        # The refusal is recorded, with the provider's own words.
        rejected = list(self.campaign.rejected)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].subscription.subscriber.email, 'r2@example.com')
        self.assertIn('mailbox does not exist', rejected[0].error)

        # And they are off the list, exactly as a webhook bounce would leave them.
        subscription = Subscriber.objects.get(email='r2@example.com').subscriptions.get()
        self.assertFalse(subscription.is_subscribed)
        self.assertEqual(subscription.unsubscribed_reason, 'bounced')

        # Nothing is owed, so there is nothing to resume and no way to loop on it.
        self.assertEqual(self.campaign.remaining_count, 0)
        self.assertFalse(self.campaign.can_resume)
        self.assertEqual(CampaignDelivery.objects.count(), 5)

    def test_a_refused_address_is_reported_on_the_page_not_buried_in_a_log(self):
        from unittest import mock

        class Picky:
            def open(self):
                pass

            def close(self):
                pass

            def send_messages(self, batch):
                if batch[0].to[0] == 'r2@example.com':
                    error = Exception('Invalid `to` field: mailbox does not exist')
                    error.status_code = 422
                    raise error
                return len(batch)

        with mock.patch('gallery.campaigns._connection', lambda: Picky()), \
                mock.patch('gallery.campaigns.RETRY_BACKOFF', 0):
            campaigns.send_campaign(self.campaign)

        page = self.client.get(reverse('gallery:campaign_edit',
                                       kwargs={'pk': self.campaign.pk}))
        self.assertContains(page, '1 address was rejected')
        self.assertContains(page, 'r2@example.com')
        self.assertContains(page, 'mailbox does not exist')
        self.assertContains(page, 'no longer on the list')

    def test_a_transient_failure_leaves_people_pending_rather_than_writing_them_off(self):
        """The distinction that matters: an outage is not the recipient's fault.

        Treating a provider having a bad minute like a bad address would unsubscribe people who
        did nothing wrong, and they would never hear from us again.
        """
        from gallery.models import Subscriber
        with self._batches_of_two(), self._dies_after(2):
            campaigns.send_campaign(self.campaign)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'failed')
        self.assertEqual(self.campaign.sent_so_far, 2)
        self.assertEqual(self.campaign.remaining_count, 3)
        self.assertEqual(self.campaign.rejected.count(), 0)
        # Still subscribed, still owed the mailing.
        for subscription in campaigns.pending(self.campaign):
            self.assertTrue(subscription.is_subscribed)
        self.assertEqual(Subscriber.objects.filter(
            subscriptions__unsubscribed_reason='bounced').count(), 0)

    def test_a_provider_refusing_everything_stops_before_working_through_the_list(self):
        """Do not spend an hour hammering an API that is down. Stop and be resumable."""
        from unittest import mock
        from gallery.models import Subscriber, Subscription

        for i in range(30):
            Subscriber.opt_in(email='extra%d@example.com' % i, sites=[self.site],
                              source=Subscription.SOURCE_SUBSCRIBE_FORM)

        class Dead:
            calls = 0

            def open(self):
                pass

            def close(self):
                pass

            def send_messages(self, batch):
                Dead.calls += 1
                error = Exception('everything is on fire')
                error.status_code = 503
                raise error

        with mock.patch('gallery.campaigns._connection', lambda: Dead()), \
                mock.patch('gallery.campaigns.RETRY_BACKOFF', 0):
            with self.assertRaises(RuntimeError):
                campaigns.send_campaign(self.campaign)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'failed')
        self.assertEqual(self.campaign.sent_so_far, 0)
        # Ten consecutive failures, each attempted three times — and then it gave up, rather
        # than trying all thirty-five.
        self.assertLess(Dead.calls, 35)
        self.assertTrue(self.campaign.can_resume)

    def test_the_throttle_paces_sends_and_does_not_sleep_when_disabled(self):
        import time as time_module
        throttle = campaigns._Throttle(50)
        started = time_module.monotonic()
        for _ in range(3):
            throttle.wait()
        # Two gaps of a fiftieth of a second; loose bound so a busy machine cannot fail it.
        self.assertGreater(time_module.monotonic() - started, 0.02)

        instant = campaigns._Throttle(0)
        started = time_module.monotonic()
        for _ in range(100):
            instant.wait()
        self.assertLess(time_module.monotonic() - started, 0.5)

    # --- Warming up a new sending domain ---

    def test_a_limited_pass_sends_part_of_the_list_and_pauses(self):
        """Sending a few hundred a day is the only real warm-up.

        Pacing inside one send does not help a domain with no history: a thousand messages on
        day one gets filtered on volume however gently they are spaced. So the ramp has to be
        across days, which means a send that deliberately stops short.
        """
        with self._locmem():
            sent = campaigns.send_campaign(self.campaign, limit=2)

        self.assertEqual(sent, 2)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'paused',
                         'stopping on purpose is not the same as failing')
        self.assertEqual(self.campaign.sent_so_far, 2)
        self.assertEqual(self.campaign.remaining_count, 3)
        self.assertTrue(self.campaign.can_resume)
        self.assertIn('was paused after 2 of 5', self.campaign.blocked_reason)

    def test_successive_passes_finish_the_list_without_repeating_anyone(self):
        # Day one is a plain limited send; every day after is a limited resume.
        with self._locmem():
            campaigns.send_campaign(self.campaign, limit=2)

        for _ in range(3):
            self.campaign.refresh_from_db()
            if not self.campaign.remaining_count:
                break
            with self._locmem():
                campaigns.send_campaign(self.campaign, resume=True, limit=2)

        addresses = [m.to[0] for m in mail.outbox]
        self.assertEqual(len(addresses), 5)
        self.assertEqual(len(set(addresses)), 5, 'somebody received it twice')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')

    def test_a_limit_larger_than_the_list_simply_finishes_it(self):
        with self._locmem():
            campaigns.send_campaign(self.campaign, limit=500)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertFalse(self.campaign.can_resume)

    def test_a_paused_campaign_says_so_on_the_page_rather_than_looking_broken(self):
        with self._locmem():
            campaigns.send_campaign(self.campaign, limit=2)
        page = self.client.get(reverse('gallery:campaign_edit',
                                       kwargs={'pk': self.campaign.pk}))
        self.assertContains(page, 'This send is paused part-way, on purpose')
        self.assertNotContains(page, 'This send stopped before it finished')

    def test_the_command_takes_a_limit(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        with self._locmem():
            call_command('send_campaign', self.campaign.pk, '--limit', '2', stdout=out)
        self.assertIn('sending at most 2', out.getvalue())
        self.assertEqual(len(mail.outbox), 2)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'paused')

    # --- The command ---

    def test_the_command_can_finish_what_the_web_send_started(self):
        """The path that survives a deploy: same engine, no web process involved."""
        from io import StringIO
        from django.core.management import call_command

        with self._batches_of_two(), self._dies_after(2):
            campaigns.send_campaign(self.campaign)

        out = StringIO()
        with self._locmem():
            call_command('send_campaign', self.campaign.pk, '--resume', stdout=out)
        self.assertIn('Sent 3 message(s)', out.getvalue())
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'sent')
        self.assertEqual(len(mail.outbox), 3)

    def test_the_command_dry_run_sends_nothing(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command('send_campaign', self.campaign.pk, '--dry-run', stdout=out)
        self.assertIn('5 still to go', out.getvalue())
        self.assertIn('r0@example.com', out.getvalue())
        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.campaign.sent_so_far, 0)


class ResendWebhookTests(TestCase):
    """Delivery events from Resend stop mail without anyone watching a dashboard."""

    def setUp(self):
        from gallery.models import Site
        self.a = Site.objects.create(name='120710', slug='120710',
                                     status=Site.STATUS_PUBLISHED)
        self.b = Site.objects.create(name='Elsewhere', slug='elsewhere',
                                     status=Site.STATUS_PUBLISHED)

    def _subscriber(self, email='sub@example.com'):
        from gallery.models import Subscriber, Subscription
        subscriber, _ = Subscriber.opt_in(
            email=email, sites=[self.a, self.b],
            source=Subscription.SOURCE_SUBSCRIBE_FORM)
        return subscriber

    def _fire(self, event_type, recipient):
        from anymail.signals import AnymailTrackingEvent, tracking
        tracking.send(sender=None, esp_name='Resend',
                      event=AnymailTrackingEvent(event_type=event_type,
                                                 recipient=recipient))

    def _state(self, subscriber):
        subscriber.refresh_from_db()
        return [(s.is_subscribed, s.unsubscribed_reason)
                for s in subscriber.subscriptions.order_by('pk')]

    def test_the_webhook_is_routed(self):
        self.assertEqual(reverse('anymail:resend_tracking_webhook'),
                         '/anymail/resend/tracking/')

    def test_a_hard_bounce_stops_every_list(self):
        """A dead address cannot be reached on any list, so narrowing to one would
        keep mailing a mailbox that does not exist."""
        from anymail.signals import EventType
        subscriber = self._subscriber('bouncer@example.com')
        self._fire(EventType.BOUNCED, 'bouncer@example.com')
        self.assertEqual(self._state(subscriber),
                         [(False, 'bounced'), (False, 'bounced')])

    def test_a_complaint_stops_every_list(self):
        """Stricter than the unsubscribe link on purpose: someone reporting us as spam
        must not keep hearing from a sibling gallery."""
        from anymail.signals import EventType
        subscriber = self._subscriber('angry@example.com')
        self._fire(EventType.COMPLAINED, 'ANGRY@Example.com')   # case-insensitive
        self.assertEqual(self._state(subscriber),
                         [(False, 'complained'), (False, 'complained')])

    def test_bounce_and_complaint_are_recorded_apart(self):
        from anymail.signals import EventType
        from gallery.models import Subscription
        one = self._subscriber('one@example.com')
        two = self._subscriber('two@example.com')
        self._fire(EventType.BOUNCED, 'one@example.com')
        self._fire(EventType.COMPLAINED, 'two@example.com')
        self.assertEqual(one.subscriptions.first().unsubscribed_reason,
                         Subscription.UNSUB_BOUNCED)
        self.assertEqual(two.subscriptions.first().unsubscribed_reason,
                         Subscription.UNSUB_COMPLAINED)

    def test_other_events_change_nothing(self):
        """We keep no behavioural data about subscribers, so opens and clicks are
        ignored rather than stored."""
        from anymail.signals import EventType
        subscriber = self._subscriber('fine@example.com')
        for event_type in (EventType.DELIVERED, EventType.OPENED, EventType.CLICKED,
                           EventType.DEFERRED, EventType.QUEUED, EventType.SENT):
            self._fire(event_type, 'fine@example.com')
        self.assertEqual(self._state(subscriber), [(True, ''), (True, '')])

    def test_an_unknown_recipient_is_ignored_not_an_error(self):
        from anymail.signals import EventType
        with self.assertLogs('gallery.webhooks', level='WARNING') as log:
            self._fire(EventType.BOUNCED, 'stranger@example.com')
        self.assertIn('not a subscriber', ''.join(log.output))

    def test_a_repeated_bounce_does_not_move_the_timestamp(self):
        from anymail.signals import EventType
        subscriber = self._subscriber('again@example.com')
        self._fire(EventType.BOUNCED, 'again@example.com')
        subscriber.refresh_from_db()
        first = subscriber.subscriptions.first().unsubscribed_at
        self._fire(EventType.BOUNCED, 'again@example.com')
        subscriber.refresh_from_db()
        self.assertEqual(subscriber.subscriptions.first().unsubscribed_at, first)

    def test_a_bounced_address_is_not_a_campaign_recipient(self):
        """The point of the whole thing: the next send must skip them."""
        from anymail.signals import EventType
        from gallery import campaigns as engine
        from gallery.models import Campaign
        self._subscriber('gone@example.com')
        keep = self._subscriber('here@example.com')
        campaign = Campaign.objects.create(site=self.a, subject='S', body_markdown='Hi')
        self._fire(EventType.BOUNCED, 'gone@example.com')
        addresses = {s.subscriber.email for s in engine.recipients(campaign)}
        self.assertEqual(addresses, {'here@example.com'})
        self.assertTrue(keep.subscriptions.filter(is_subscribed=True).exists())




class SubscribePagesTests(TestCase):
    """The public subscribe form, the kiosk form, and being able to find them."""

    def setUp(self):
        from gallery.models import Site
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 Tenth Street', city='Berkeley', state='CA', postal_code='94710')

    # `address` is the honeypot field (settings.HONEYPOT_FIELD_NAME); it must be present
    # and empty or django-honeypot rejects the post.
    def _post(self, url, email, **extra):
        data = {'first_name': 'New', 'last_name': 'Person', 'email': email, 'address': ''}
        data.update(extra)
        return self.client.post(url, data, follow=True)

    def test_the_form_writes_to_our_own_table(self):
        from gallery.models import Subscriber, Subscription
        r = self._post(reverse('subscribe'), 'new@example.com')
        self.assertEqual(r.status_code, 200)
        subscriber = Subscriber.objects.get(email='new@example.com')
        self.assertEqual(subscriber.full_name, 'New Person')
        subscription = subscriber.subscriptions.get()
        self.assertTrue(subscription.is_subscribed)
        self.assertEqual(subscription.source, Subscription.SOURCE_SUBSCRIBE_FORM)

    def test_the_kiosk_form_records_where_it_came_from(self):
        from gallery.models import Subscriber, Subscription
        with self.settings(KIOSK_TOKEN='tok'):
            self._post(reverse('subscribe_kiosk', kwargs={'token': 'tok'}), 'k@example.com')
        subscriber = Subscriber.objects.get(email='k@example.com')
        self.assertEqual(subscriber.subscriptions.get().source, Subscription.SOURCE_KIOSK)

    def test_the_kiosk_needs_its_token(self):
        from gallery.models import Subscriber
        with self.settings(KIOSK_TOKEN='tok'):
            r = self.client.get(reverse('subscribe_kiosk', kwargs={'token': 'wrong'}))
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Subscriber.objects.exists())

    def test_subscribing_twice_does_not_duplicate_the_person(self):
        from gallery.models import Subscriber
        self._post(reverse('subscribe'), 'twice@example.com')
        self._post(reverse('subscribe'), 'TWICE@Example.com')
        self.assertEqual(Subscriber.objects.filter(email='twice@example.com').count(), 1)

    # --- The welcome email ---

    def test_a_new_subscriber_gets_a_welcome_email_at_once(self):
        """Chosen over double opt-in: no gate, so no signup is lost, but a dead address bounces
        on this one message and the webhook removes it before any campaign goes out."""
        self._post(reverse('subscribe'), 'new@example.com')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['new@example.com'])
        self.assertIn('mailing list', message.subject.lower())

    def test_the_welcome_email_makes_leaving_easy(self):
        """The other job it does: somebody added by a stranger has to be able to get out."""
        self._post(reverse('subscribe'), 'new@example.com')
        body = mail.outbox[0].alternatives[0][0]
        self.assertIn('/unsubscribe/', body)
        self.assertIn('unsubscribe here', body)
        # One-click, so the button Gmail renders works on it too.
        self.assertIn('List-Unsubscribe', mail.outbox[0].extra_headers)
        self.assertEqual(mail.outbox[0].extra_headers['List-Unsubscribe-Post'],
                         'List-Unsubscribe=One-Click')

    def test_the_unsubscribe_link_in_a_welcome_email_actually_works(self):
        from gallery.models import Subscriber
        self._post(reverse('subscribe'), 'new@example.com')
        body = mail.outbox[0].alternatives[0][0]
        start = body.index('/unsubscribe/')
        path = body[start:body.index('"', start)]

        self.client.post(path)
        subscription = Subscriber.objects.get(email='new@example.com').subscriptions.get()
        self.assertFalse(subscription.is_subscribed)

    def test_the_kiosk_welcomes_too(self):
        """An address mistyped on a tablet at an opening is exactly the kind that would
        otherwise bounce on every mailing for years."""
        with self.settings(KIOSK_TOKEN='tok'):
            self._post(reverse('subscribe_kiosk', kwargs={'token': 'tok'}), 'k@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['k@example.com'])

    def test_resubmitting_the_form_does_not_send_another_welcome(self):
        """Otherwise the form is a way to pester somebody who is already on the list."""
        self._post(reverse('subscribe'), 'twice@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self._post(reverse('subscribe'), 'twice@example.com')
        self.assertEqual(len(mail.outbox), 1)

    def test_someone_rejoining_after_leaving_is_welcomed_again(self):
        from gallery.models import Subscriber
        self._post(reverse('subscribe'), 'back@example.com')
        Subscriber.objects.get(email='back@example.com').unsubscribe_all()
        mail.outbox.clear()

        self._post(reverse('subscribe'), 'back@example.com')
        self.assertEqual(len(mail.outbox), 1)

    def test_a_mail_failure_does_not_cost_the_subscription(self):
        """They are already subscribed by the time the welcome is sent. Refusing to subscribe
        somebody because our own mail server was briefly unhappy is the worse outcome."""
        from unittest import mock
        from gallery.models import Subscriber

        with mock.patch('eatart.views.subscribe.EmailMultiAlternatives.send',
                        side_effect=RuntimeError('mail server down')):
            r = self._post(reverse('subscribe'), 'resilient@example.com')

        self.assertEqual(r.status_code, 200)
        self.assertTrue(Subscriber.objects.get(
            email='resilient@example.com').subscriptions.get().is_subscribed)

    def test_the_welcome_email_does_not_go_through_the_campaign_provider(self):
        """A spam complaint about a newsletter must not be able to swallow this."""
        import inspect
        from eatart.views import subscribe as view_module
        source = inspect.getsource(view_module.send_welcome)
        self.assertNotIn('_connection', source,
                         'the welcome email must use the transactional backend')

    def test_subscribe_is_reachable_from_the_nav_with_a_default_venue(self):
        """It used to be gated on mailing_list_enabled, which only the contact view set —
        so everywhere else it fell back to "not current_site", and with a default venue
        configured that is never true. The link was missing site-wide."""
        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            import importlib
            from eatart import context_processors
            importlib.reload(context_processors)
            try:
                for path in ('/', reverse('contact'), self.site.get_absolute_url()):
                    with self.subTest(path=path):
                        body = self.client.get(path, follow=True).content.decode()
                        self.assertIn('>Subscribe</a>', body)
            finally:
                importlib.reload(context_processors)

    def test_the_contact_page_always_offers_the_list(self):
        """No provider to configure any more, so there is no state in which the list
        should be hidden."""
        body = self.client.get(reverse('contact')).content.decode()
        self.assertIn('Mailing List', body)
        self.assertIn(reverse('subscribe'), body)




class ImportSubscribersTests(TestCase):
    """Merging Mailchimp exports. The rule that matters: no path through this may end
    with someone subscribed who said no in any export."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _csv(self, name, rows, header='Email Address,First Name,Last Name,Status'):
        import os
        path = os.path.join(self.tmp, name)
        with open(path, 'w', newline='') as handle:
            handle.write(header + '\n')
            for row in rows:
                handle.write(','.join(row) + '\n')
        return path

    def _run(self, *paths, **opts):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('import_subscribers', *paths, stdout=out, **opts)
        return out.getvalue()

    def test_several_files_in_one_run(self):
        from gallery.models import Subscriber
        a = self._csv('a.csv', [('ana@example.com', 'Ana', 'Ruiz', 'subscribed')])
        b = self._csv('b.csv', [('bo@example.com', 'Bo', 'Chen', 'subscribed')])
        out = self._run(a, b)
        self.assertIn('2 file(s)', out)
        self.assertEqual(Subscriber.objects.count(), 2)

    def test_the_same_person_across_files_is_one_record(self):
        from gallery.models import Subscriber
        a = self._csv('a.csv', [('Ana@Example.com', 'Ana', 'Ruiz', 'subscribed')])
        b = self._csv('b.csv', [('ANA@example.com', 'Ana', 'Ruiz', 'subscribed')])
        out = self._run(a, b)
        self.assertEqual(Subscriber.objects.count(), 1)
        self.assertIn('duplicate row(s) merged', out)

    def test_repeated_rows_within_one_file_are_merged(self):
        from gallery.models import Subscriber
        path = self._csv('dupes.csv', [
            ('ana@example.com', 'Ana', 'Ruiz', 'subscribed'),
            ('ana@example.com', 'Ana', 'Ruiz', 'subscribed'),
            ('ana@example.com', 'Ana', 'Ruiz', 'subscribed')])
        out = self._run(path)
        self.assertEqual(Subscriber.objects.count(), 1)
        self.assertIn('3 row(s)', out)
        self.assertIn('1 distinct people', out)

    def test_an_opt_out_anywhere_wins(self):
        """Listed as subscribed in one export and unsubscribed in another: being
        subscribed somewhere must not undo having said no."""
        from gallery.models import Subscriber
        yes = self._csv('yes.csv', [('ana@example.com', 'Ana', 'Ruiz', 'subscribed')])
        no = self._csv('no.csv', [('ana@example.com', 'Ana', 'Ruiz', 'unsubscribed')])
        for order in ((yes, no), (no, yes)):
            with self.subTest(order=[p[-6:] for p in order]):
                Subscriber.objects.all().delete()
                self._run(*order)
                subscription = Subscriber.objects.get(email='ana@example.com').subscriptions.get()
                self.assertFalse(subscription.is_subscribed)

    def test_cleaned_and_unknown_statuses_are_treated_as_opted_out(self):
        from gallery.models import Subscriber
        path = self._csv('mixed.csv', [
            ('a@example.com', 'A', 'One', 'cleaned'),
            ('b@example.com', 'B', 'Two', 'archived'),
            ('c@example.com', 'C', 'Three', 'something-new'),
            ('d@example.com', 'D', 'Four', 'subscribed')])
        self._run(path)
        subscribed = {s.subscriber.email for s in
                      Subscriber.objects.first().subscriptions.model.objects.filter(
                          is_subscribed=True)}
        self.assertEqual(subscribed, {'d@example.com'})

    def test_a_blank_name_in_one_row_does_not_erase_it_from_another(self):
        from gallery.models import Subscriber
        path = self._csv('names.csv', [
            ('bo@example.com', '', '', 'subscribed'),
            ('bo@example.com', 'Bo', 'Chen', 'subscribed')])
        self._run(path)
        self.assertEqual(Subscriber.objects.get(email='bo@example.com').full_name, 'Bo Chen')

    def test_an_existing_corrected_name_is_not_overwritten(self):
        """A name fixed here outranks the export it was fixed from; a blank one is
        filled in."""
        from gallery.models import Subscriber
        Subscriber.objects.create(email='ana@example.com', first_name='Ana',
                                  last_name='Ruiz-Corrected')
        Subscriber.objects.create(email='bo@example.com')
        path = self._csv('again.csv', [
            ('ana@example.com', 'Ana', 'Ruiz', 'subscribed'),
            ('bo@example.com', 'Bo', 'Chen', 'subscribed')])
        self._run(path)
        self.assertEqual(Subscriber.objects.get(email='ana@example.com').last_name,
                         'Ruiz-Corrected')
        self.assertEqual(Subscriber.objects.get(email='bo@example.com').full_name, 'Bo Chen')

    def test_running_it_twice_changes_nothing(self):
        from gallery.models import Subscriber
        path = self._csv('twice.csv', [
            ('ana@example.com', 'Ana', 'Ruiz', 'subscribed'),
            ('bo@example.com', 'Bo', 'Chen', 'unsubscribed')])
        self._run(path)
        before = [(s.email, s.full_name, s.subscriptions.get().is_subscribed,
                   s.subscriptions.get().unsubscribed_at)
                  for s in Subscriber.objects.order_by('email')]
        out = self._run(path)
        after = [(s.email, s.full_name, s.subscriptions.get().is_subscribed,
                  s.subscriptions.get().unsubscribed_at)
                 for s in Subscriber.objects.order_by('email')]
        self.assertEqual(before, after)
        self.assertIn('0 new, 2 already present', out)

    def test_dry_run_writes_nothing(self):
        from gallery.models import Subscriber
        path = self._csv('dry.csv', [('ana@example.com', 'Ana', 'Ruiz', 'subscribed')])
        out = self._run(path, dry_run=True)
        self.assertIn('Nothing was written', out)
        self.assertFalse(Subscriber.objects.exists())

    def test_alternative_column_spellings_are_accepted(self):
        from gallery.models import Subscriber
        path = self._csv('alt.csv', [('ana@example.com', 'Ana', 'Ruiz', 'subscribed')],
                         header='email_address,fname,lname,member status')
        self._run(path)
        self.assertEqual(Subscriber.objects.get(email='ana@example.com').full_name, 'Ana Ruiz')

    def test_a_file_with_no_email_column_is_refused(self):
        from django.core.management.base import CommandError
        path = self._csv('bad.csv', [('x', 'y')], header='name,notes')
        with self.assertRaises(CommandError):
            self._run(path)

    def test_rows_without_a_usable_email_are_counted_and_skipped(self):
        from gallery.models import Subscriber
        path = self._csv('partial.csv', [
            ('', 'No', 'Email', 'subscribed'),
            ('not-an-address', 'Bad', 'One', 'subscribed'),
            ('ana@example.com', 'Ana', 'Ruiz', 'subscribed')])
        out = self._run(path)
        self.assertIn('2 row(s) had no usable email', out)
        self.assertEqual(Subscriber.objects.count(), 1)

    def test_importing_into_a_named_venue(self):
        from gallery.models import Site, Subscriber
        site = Site.objects.create(name='120710', slug='120710',
                                   status=Site.STATUS_PUBLISHED)
        path = self._csv('venue.csv', [('ana@example.com', 'Ana', 'Ruiz', 'subscribed')])
        self._run(path, site='120710')
        subscription = Subscriber.objects.get(email='ana@example.com').subscriptions.get()
        self.assertEqual(subscription.site, site)

    def test_an_unknown_venue_slug_is_refused(self):
        from django.core.management.base import CommandError
        path = self._csv('venue.csv', [('ana@example.com', 'Ana', 'Ruiz', 'subscribed')])
        with self.assertRaises(CommandError):
            self._run(path, site='no-such-venue')




class PrivacyPageTests(TestCase):
    """The policy has to be reachable, venue-aware, and true. The last one is what the
    tests can actually help with: each claim below is paired with the behaviour that
    makes it honest, so changing the behaviour breaks the claim."""

    def setUp(self):
        from gallery.models import Site
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 Tenth Street', city='Berkeley', state='CA',
            postal_code='94710', email='info@120710.art')

    def test_reachable_scoped_and_unscoped(self):
        for url in (reverse('privacy'),
                    reverse('site_privacy', kwargs={'site_slug': self.site.slug})):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, '120710')
                # A route for access, correction and deletion has to stay open — but it
                # points at the contact page now. Printing the address on the one page a
                # scraper is guaranteed to read is what the gallery stopped doing.
                self.assertContains(r, reverse('contact'))
                self.assertNotContains(r, 'mailto:info@120710.art')

    def test_an_unpublished_venue_has_no_public_policy_page(self):
        from gallery.models import Site
        hidden = Site.objects.create(name='Hidden', slug='hidden',
                                     status=Site.STATUS_DRAFT)
        r = self.client.get(reverse('site_privacy', kwargs={'site_slug': hidden.slug}))
        self.assertEqual(r.status_code, 404)

    def test_linked_from_the_nav_and_the_subscribe_form(self):
        self.assertContains(self.client.get('/', follow=True), '>Privacy</a>')
        self.assertContains(self.client.get(reverse('subscribe')), 'privacy page')

    def test_every_campaign_links_it(self):
        """A mailing list's policy has to be reachable from the mail itself, not only
        from a site someone would have to go looking for."""
        from gallery import campaigns as engine
        from gallery.models import Campaign, Subscriber, Subscription
        subscriber, _ = Subscriber.opt_in(
            email='s@example.com', sites=[self.site],
            source=Subscription.SOURCE_SUBSCRIBE_FORM)
        campaign = Campaign.objects.create(site=self.site, subject='S',
                                           body_markdown='Hi')
        html = engine.render_campaign(campaign, subscriber.subscriptions.get())
        self.assertIn(reverse('site_privacy', kwargs={'site_slug': self.site.slug}), html)

    def test_the_no_tracking_claim_is_true(self):
        """It says our mail carries no tracking pixel and no rewritten links, and that
        we discard open/click events. Both halves are checked here."""
        from anymail.signals import AnymailTrackingEvent, EventType, tracking
        from gallery import campaigns as engine
        from gallery.models import Campaign, Subscriber, Subscription
        subscriber, _ = Subscriber.opt_in(
            email='t@example.com', sites=[self.site],
            source=Subscription.SOURCE_SUBSCRIBE_FORM)
        campaign = Campaign.objects.create(site=self.site, subject='S',
                                           body_markdown='Read [this](https://example.com/a).')
        html = engine.render_campaign(campaign, subscriber.subscriptions.get())

        # The link the author wrote survives verbatim — not rewritten through a tracker.
        self.assertIn('https://example.com/a', html)
        # No 1x1 beacon.
        self.assertNotIn('width="1"', html)

        # And an open reported anyway changes nothing.
        before = list(subscriber.subscriptions.values_list('is_subscribed', flat=True))
        tracking.send(sender=None, esp_name='Resend',
                      event=AnymailTrackingEvent(event_type=EventType.OPENED,
                                                 recipient='t@example.com'))
        self.assertEqual(list(subscriber.subscriptions.values_list('is_subscribed', flat=True)),
                         before)

    def test_the_contact_details_are_not_public_claim_is_true(self):
        """It says an artist's email, phone, postal address and Venmo are not public and
        are kept out of the machine-readable listings."""
        from django.test import RequestFactory
        from eatart.schemaorg.mappers import artist_to_schema, schema_to_dict
        artist = Artist.objects.create(
            first_name='Pia', last_name='Private', email='pia@example.com',
            phone='555-0100', venmo='@pia', street='1 Test Lane', bio='A bio.')
        art = Artwork.objects.create(name='W', end_year=2025)
        art.artists.add(artist)
        show = Show.objects.create(name='Pub', status=Show.STATUS_PUBLISHED,
                                   start=datetime.date.today(), end=datetime.date.today())
        art.shows.add(show)

        body = self.client.get(artist.get_absolute_url(), follow=True).content.decode()
        for secret in ('pia@example.com', '555-0100', '@pia', '1 Test Lane'):
            self.assertNotIn(secret, body)
        self.assertIn('A bio.', body)      # the public half still is public

        blob = str(schema_to_dict(artist_to_schema(artist, RequestFactory().get('/'))))
        for secret in ('pia@example.com', '555-0100', '@pia'):
            self.assertNotIn(secret, blob)




class SubscriberManagementTests(TestCase):
    """Staff pages for the list. The reason this exists is that somebody emails asking to
    be taken off and there has to be somewhere to do it — so that path is tested first."""

    def setUp(self):
        from gallery.models import Site, Subscriber, Subscription
        self.a = Site.objects.create(name='120710', slug='120710',
                                     status=Site.STATUS_PUBLISHED)
        self.b = Site.objects.create(name='Elsewhere', slug='elsewhere',
                                     status=Site.STATUS_PUBLISHED)
        self.both, _ = Subscriber.opt_in(
            email='both@example.com', first_name='Both', last_name='Lists',
            sites=[self.a, self.b], source=Subscription.SOURCE_SUBSCRIBE_FORM)
        self.bounced, _ = Subscriber.opt_in(
            email='gone@example.com', sites=[self.a],
            source=Subscription.SOURCE_SUBSCRIBE_FORM)
        self.bounced.unsubscribe_all(reason=Subscription.UNSUB_BOUNCED)
        self.staff = User.objects.create_user(
            username='subs@example.com', email='subs@example.com', password='pw')
        add_staff_role(self.staff)
        self.client.force_login(self.staff)

    def _lists(self, subscriber):
        subscriber.refresh_from_db()
        return sorted((s.list_name, s.is_subscribed) for s in subscriber.subscriptions.all())

    def test_removing_someone_from_one_list_leaves_the_others(self):
        subscription = self.both.subscriptions.get(site=self.a)
        r = self.client.post(reverse('gallery:subscription_unsubscribe',
                                     kwargs={'pk': subscription.pk}), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._lists(self.both), [('120710', False), ('Elsewhere', True)])

    def test_removing_someone_from_everything(self):
        self.client.post(reverse('gallery:subscriber_unsubscribe_all',
                                 kwargs={'pk': self.both.pk}), follow=True)
        self.assertEqual(self._lists(self.both), [('120710', False), ('Elsewhere', False)])

    def test_a_removal_is_recorded_as_requested_not_as_a_bounce(self):
        """The reason has to stay honest: it decides whether an address is ever tried
        again."""
        from gallery.models import Subscription
        subscription = self.both.subscriptions.get(site=self.a)
        self.client.post(reverse('gallery:subscription_unsubscribe',
                                 kwargs={'pk': subscription.pk}))
        subscription.refresh_from_db()
        self.assertEqual(subscription.unsubscribed_reason, Subscription.UNSUB_REQUESTED)

    def test_someone_can_be_added_back_when_they_ask(self):
        subscription = self.both.subscriptions.get(site=self.a)
        self.client.post(reverse('gallery:subscription_unsubscribe',
                                 kwargs={'pk': subscription.pk}))
        self.client.post(reverse('gallery:subscription_resubscribe',
                                 kwargs={'pk': subscription.pk}))
        self.assertEqual(self._lists(self.both), [('120710', True), ('Elsewhere', True)])

    def test_a_bounced_address_offers_no_add_back_button(self):
        """Undoing a bounce or complaint from here would make the suppression record
        meaningless, so the button is only offered for a plain request."""
        body = self.client.get(reverse('gallery:subscriber_list')).content.decode()
        self.assertIn('hard bounce', body.lower())
        subscription = self.bounced.subscriptions.get()
        self.assertNotIn(reverse('gallery:subscription_resubscribe',
                                 kwargs={'pk': subscription.pk}), body)

    def test_adding_one_person_by_hand(self):
        from gallery.models import Subscriber, Subscription
        self.client.post(reverse('gallery:subscriber_add'),
                         {'email': 'HAND@Example.com', 'first_name': 'By',
                          'last_name': 'Hand', 'site': 'elsewhere'})
        person = Subscriber.objects.get(email='hand@example.com')   # lower-cased
        self.assertEqual(person.full_name, 'By Hand')
        subscription = person.subscriptions.get()
        self.assertEqual(subscription.site, self.b)
        self.assertEqual(subscription.source, Subscription.SOURCE_MANUAL)

    def test_adding_a_bad_address_is_refused(self):
        from gallery.models import Subscriber
        before = Subscriber.objects.count()
        self.client.post(reverse('gallery:subscriber_add'), {'email': 'not-an-address'})
        self.assertEqual(Subscriber.objects.count(), before)

    def test_deleting_someone_entirely(self):
        from gallery.models import Subscriber, Subscription
        self.client.post(reverse('gallery:subscriber_delete'.replace('x', 'x'),
                                 kwargs={'pk': self.bounced.pk}))
        self.assertFalse(Subscriber.objects.filter(email='gone@example.com').exists())
        # And their subscriptions went with them.
        self.assertFalse(Subscription.objects.filter(
            subscriber__email='gone@example.com').exists())

    def test_search_and_filters(self):
        url = reverse('gallery:subscriber_list')
        cases = [
            ({'q': 'both'}, True, False),
            ({'q': 'gone'}, False, True),
            ({'status': 'unsubscribed'}, False, True),
            ({'status': 'subscribed'}, True, False),
            ({'list': 'elsewhere'}, True, False),
        ]
        for params, wants_both, wants_gone in cases:
            with self.subTest(**params):
                body = self.client.get(url, params).content.decode()
                self.assertEqual('both@example.com' in body, wants_both)
                self.assertEqual('gone@example.com' in body, wants_gone)

    def test_someone_on_two_lists_who_left_one_is_not_listed_as_unsubscribed(self):
        subscription = self.both.subscriptions.get(site=self.a)
        # follow=True so the "removed from…" flash message is consumed by the redirect.
        # Left queued it renders on the next page and names the address, which would look
        # like the filter had matched them.
        self.client.post(reverse('gallery:subscription_unsubscribe',
                                 kwargs={'pk': subscription.pk}), follow=True)
        body = self.client.get(reverse('gallery:subscriber_list'),
                               {'status': 'unsubscribed'}).content.decode()
        self.assertNotIn('both@example.com', body)

    def test_the_counts_match_what_a_campaign_would_send_to(self):
        from gallery import campaigns as engine
        from gallery.models import Campaign
        campaign = Campaign.objects.create(site=self.a, subject='S', body_markdown='Hi')
        expected = engine.recipients(campaign).count()
        body = self.client.get(reverse('gallery:subscriber_list')).content.decode()
        self.assertIn('120710 <strong>%d</strong>' % expected,
                      ' '.join(body.split()))

    def test_only_staff_get_in(self):
        from gallery.models import Subscriber
        actions = [
            reverse('gallery:subscriber_list'),
            reverse('gallery:subscriber_delete', kwargs={'pk': self.both.pk}),
            reverse('gallery:subscriber_unsubscribe_all', kwargs={'pk': self.both.pk}),
        ]
        self.client.logout()
        for url in actions:
            with self.subTest(url=url, who='anonymous'):
                self.assertEqual(self.client.post(url).status_code, 302)

        artist = User.objects.create_user(
            username='nope2@example.com', email='nope2@example.com', password='pw')
        self.client.force_login(artist)
        for url in actions:
            with self.subTest(url=url, who='non-staff'):
                self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(Subscriber.objects.filter(email='both@example.com').exists())



class HowToAnchorTests(TestCase):
    """Every link into the help system must land on a guide that exists.

    The show page linked #how-to-submit-artwork-to-an-open-call-show long after that
    guide had been retitled, so the link silently dumped people at the top of the page.
    Anchors default to a slug of the title, so a retitle breaks every link to it with
    nothing to notice.

    Guides now live on their own pages (`howto_guide`), which changes the failure mode
    rather than removing it: a wrong anchor is now a 404 instead of a silent no-op, but
    only if something checks that the anchor is real, because `{% url %}` will happily
    reverse `howto_guide` with any string at all.
    """

    def _valid_anchors(self):
        from eatart.role_docs import HOW_TO_GUIDES
        from eatart.views.public import guide_anchor
        return {guide_anchor(g) for g in HOW_TO_GUIDES}

    def _templates(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for path in root.rglob('*.html'):
            if any(part in ('env', 'node_modules', '.claude') for part in path.parts):
                continue
            yield path

    def test_submit_guide_is_one_illustrated_guide_for_every_reader(self):
        """Submitting is one task, documented once, illustrated for everyone.

        It used to ship as two mutually exclusive versions — a beginner one for
        signed-out readers and a shorter one for signed-in readers. That meant a
        signed-in artist following the show page's "How submitting works" link got the
        un-illustrated version of the very flow the app walks them through, because only
        the public variant had screenshots. The app drives a new submitter through signup
        and sign-in as part of submitting, so the whole arc is one guide now and readers
        who already have an account skip the first steps.
        """
        url = reverse('howto_guide', args=['submit-artwork'])

        user = User.objects.create_user(
            username='hw@example.com', email='hw@example.com', password='pw')
        Artist.objects.create(user=user, first_name='How', last_name='To',
                              email='hw@example.com')

        bodies = {}
        for label in ('signed out', 'signed in'):
            if label == 'signed in':
                self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f'{label} cannot open the guide')
            bodies[label] = response.content.decode()

        self.assertIn('Create an account', bodies['signed out'])
        self.assertEqual(
            'Create an account' in bodies['signed in'], True,
            'a signed-in reader must get the same guide, including the account steps '
            'they can skip — not a separate stripped-down version')

    def test_visible_guide_anchors_are_unique_per_reader(self):
        """Routing resolves an anchor against the guides a reader can see.

        Anchors are allowed to repeat across HOW_TO_GUIDES — that is how a `public_only`
        guide and its role-gated counterpart can share one link. What must hold is that
        no single reader ever sees two guides with the same anchor, because `howto_guide`
        would silently serve whichever came first in the list and the other would be
        unreachable. That is exactly the bug the submit guide had.
        """
        from collections import Counter
        from eatart.views.public import _visible_guides, guide_anchor

        for role in (None, 'artist', 'juror', 'curator', 'staff'):
            counts = Counter(guide_anchor(g) for g in _visible_guides(role))
            clashes = {a: n for a, n in counts.items() if n > 1}
            self.assertEqual(
                clashes, {},
                f'a reader with role {role!r} sees more than one guide at {clashes} — '
                f'only the first would ever be reachable')

    def test_every_guide_has_its_own_reachable_page(self):
        """The index only lists guides, so an unreachable one is invisible, not obvious."""
        from eatart.role_docs import HOW_TO_GUIDES
        from eatart.views.public import guide_anchor

        public = [g for g in HOW_TO_GUIDES if g['roles'] is None]
        for guide in public:
            url = reverse('howto_guide', args=[guide_anchor(guide)])
            self.assertEqual(self.client.get(url).status_code, 200,
                             f'{guide["title"]} is listed but does not resolve')

    def test_a_guide_for_another_role_is_not_readable_by_url(self):
        """Visibility has to be enforced on the page, not just in the listing.

        Otherwise both submit-artwork versions would be readable at the same URL
        depending on nothing, and the role filtering on the index would be decorative.
        """
        self.assertEqual(
            self.client.get(reverse('howto_guide',
                                    args=['regenerate-howto-screenshots'])).status_code,
            404)
        self.assertEqual(
            self.client.get(reverse('howto_guide', args=['no-such-guide'])).status_code,
            404)

    def test_index_lists_guides_with_descriptions(self):
        from eatart.role_docs import HOW_TO_GUIDES
        missing = [g['title'] for g in HOW_TO_GUIDES if not g.get('summary')]
        self.assertEqual(
            missing, [],
            'the index shows only a title and a summary, so a guide without one is '
            f'undescribed to every reader: {missing}')

        body = self.client.get(reverse('howto')).content.decode()
        self.assertIn(reverse('howto_guide', args=['submit-artwork']), body)
        self.assertIn(reverse('howto_reference'), body)

    def test_no_template_links_to_a_missing_guide(self):
        valid = self._valid_anchors()
        broken = []
        for path in self._templates():
            text = path.read_text()
            # New form: {% url 'howto_guide' 'anchor' %}
            for m in re.finditer(
                    r"\{%\s*url\s*'howto_guide'\s*'([a-z0-9-]+)'\s*%\}", text):
                if m.group(1) not in valid:
                    broken.append(f'{path.name} → howto_guide {m.group(1)}')
            # Old form: {% url 'howto' %}#anchor. Guides are no longer sections on the
            # index, so this now silently lands the reader on a list of links instead of
            # the guide they asked for — worth failing on rather than tolerating.
            for m in re.finditer(r"\{%\s*url\s*'howto'\s*%\}#([a-z0-9-]+)", text):
                broken.append(
                    f'{path.name} → #{m.group(1)} (guides have their own pages now; '
                    f"use {{% url 'howto_guide' '{m.group(1)}' %}})")
        self.assertEqual(broken, [], 'broken links into the help system')


class HowToImageKeyTests(TestCase):
    """Each guide's screenshots must belong to exactly one guide.

    Screenshots are addressed by step number under an image key, so two guides sharing
    a key caption each other's prose with the wrong pictures — and silently, since both
    directories exist and both render. The submit-artwork guide is the live hazard: its
    public and signed-in versions deliberately share an `anchor` (one link has to serve
    either reader), and the image key falls back to the anchor, so the public one has to
    override it. See eatart/howto_images.py.
    """

    def test_image_keys_are_unique(self):
        from collections import Counter
        from eatart.howto_images import image_key
        from eatart.role_docs import HOW_TO_GUIDES

        counts = Counter(image_key(g) for g in HOW_TO_GUIDES)
        clashes = {k: n for k, n in counts.items() if n > 1}
        self.assertEqual(
            clashes, {},
            'these image keys are used by more than one guide, so their screenshots '
            "would appear under another guide's steps — give each an explicit "
            "'image_key'")

    def test_captured_images_are_paired_with_their_steps(self):
        """A published image only renders against the step it was captured for.

        Guides gain and lose steps; images are numbered, so a stale manifest would shift
        every picture onto the wrong sentence. The images live on S3 and only the
        manifest is committed, so this is the one check that can catch that in CI —
        nothing else in the repo knows what was published.
        """
        from eatart.howto_images import image_key, load_manifest, steps_with_images
        from eatart.role_docs import HOW_TO_GUIDES

        by_key = {image_key(g): g for g in HOW_TO_GUIDES}
        manifest = load_manifest()

        unknown = sorted(set(manifest) - set(by_key))
        self.assertEqual(
            unknown, [],
            'eatart/howto_manifest.json has entries for guides that no longer exist: '
            f'{unknown} — was a guide renamed without its image_key?')

        for key, entries in manifest.items():
            steps = by_key[key]['steps']
            overrun = sorted(int(n) for n in entries if int(n) > len(steps))
            self.assertEqual(
                overrun, [],
                f'{key}: manifest has images for steps {overrun} but the guide only has '
                f'{len(steps)} — re-run `manage.py capture_howto {key}` and --publish')
            for number, entry in entries.items():
                self.assertTrue(
                    entry.get('key') and entry.get('width') and entry.get('height'),
                    f'{key} step {number}: needs key, width and height — width/height '
                    f'are what keep the screenshot rendering at a legible 1:1 size')

        for guide in HOW_TO_GUIDES:
            self.assertEqual(
                [s['text'] for s in steps_with_images(guide)], list(guide['steps']),
                f'{image_key(guide)}: step prose must survive pairing with its images')


class ShowActionsTests(TestCase):
    """Show-page controls: plain buttons for everyone, small menus for curators."""

    def setUp(self):
        today = datetime.date.today()
        self.show = Show.objects.create(
            name='Now Showing', status=Show.STATUS_PUBLISHED,
            start=today - datetime.timedelta(days=1), end=today + datetime.timedelta(days=20))
        art = Artwork.objects.create(name='W', end_year=2025)
        art.shows.add(self.show)
        from gallery.models import WallPlacement
        WallPlacement.objects.create(show=self.show, artwork=art, wall='N',
                                     x_in=0, y_in=60, z_in=-288)

    def _controls(self):
        body = self.client.get(self.show.get_absolute_url()).content.decode()
        seg = body[body.index('show-actions'):body.index('section-label')]
        buttons = [b.strip() for b in re.findall(
            r'<a class="card__link" href="[^"]*"[^>]*>([^<]+)</a>', seg)]
        menus = {}
        for m in re.finditer(
                r'dropdown-toggle[^>]*>\s*([^<]+?)\s*</button>.*?<ul class="dropdown-menu">(.*?)</ul>',
                seg, re.S):
            menus[m.group(1).strip()] = [i.strip() for i in re.findall(
                r'dropdown-item[^>]*>\s*([^<]+?)\s*</', m.group(2))]
        return buttons, menus

    def test_visitor_gets_buttons_and_no_menus(self):
        buttons, menus = self._controls()
        self.assertEqual(buttons, ['2D View', '3D View', 'Checklist'])
        self.assertEqual(menus, {})

    def test_curator_gets_the_same_buttons_plus_grouped_menus(self):
        staff = User.objects.create_user(
            username='sa@example.com', email='sa@example.com', password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        buttons, menus = self._controls()
        self.assertEqual(buttons, ['2D View', '3D View', 'Checklist'])
        # Manage comes first, immediately after the Checklist button.
        self.assertEqual(list(menus), ['Manage', 'Curate', 'Produce', 'Logistics'])
        # Instagram is a curator tool for preparing posts, not a way to view the show.
        self.assertIn('Instagram', menus['Produce'])
        self.assertNotIn('Instagram', buttons)
        for label, items in menus.items():
            self.assertLessEqual(len(items), 5, '%s menu is too long' % label)

    def test_no_action_bar_at_all_when_the_viewer_has_nothing(self):
        """An open call seen by a non-curator offers no controls; the bar must not
        render, or its padding and borders leave an empty strip on the card."""
        today = datetime.date.today()
        oc = Show.objects.create(
            name='Open Studio', status=Show.STATUS_OPEN_CALL, submission_type='open',
            start=today + datetime.timedelta(days=60), end=today + datetime.timedelta(days=90))
        body = self.client.get(oc.get_absolute_url(), follow=True).content.decode()
        self.assertNotIn('show-actions', body)
        self.assertIsNone(re.search(r'<div class="card__info[^"]*">\s*</div>', body))

    def test_empty_groups_are_not_rendered(self):
        """A group with nothing in it must not leave a stray control or spacing."""
        from gallery.show_actions import show_actions
        actions = show_actions(self.show)          # no permissions at all
        self.assertEqual(actions['menus'], [])
        actions = show_actions(self.show, can_manage=True)
        self.assertNotIn('Curate', [m['label'] for m in actions['menus']])

    def test_catalog_and_placards_buttons_are_gone_but_still_reachable(self):
        staff = User.objects.create_user(
            username='sb@example.com', email='sb@example.com', password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        buttons, menus = self._controls()
        flat = buttons + [i for items in menus.values() for i in items]
        self.assertNotIn('Catalog', flat)
        self.assertNotIn('Placards', flat)
        self.assertEqual(self.client.get(
            reverse('gallery:show_catalog', kwargs={'slug': self.show.slug})).status_code, 200)
        self.assertEqual(self.client.get(
            reverse('gallery:show_placards_detail', kwargs={'slug': self.show.slug})).status_code, 200)


class SiteShowListTests(TestCase):
    """/site/<slug>/shows/ is the shows list scoped to one venue — same view and
    template, so it keeps New, Slideshow, tag filtering and the submit buttons."""

    def setUp(self):
        today = datetime.date.today()
        from gallery.models import Site
        self.site = Site.objects.create(name='120710', slug='120710',
                                        status=Site.STATUS_PUBLISHED)
        other = Site.objects.create(name='Elsewhere', slug='elsewhere',
                                    status=Site.STATUS_PUBLISHED)
        self.mine = Show.objects.create(
            name='Mine Now', status=Show.STATUS_PUBLISHED,
            start=today - datetime.timedelta(days=1), end=today + datetime.timedelta(days=20))
        self.mine.sites.add(self.site)
        self.theirs = Show.objects.create(
            name='Theirs', status=Show.STATUS_PUBLISHED,
            start=today - datetime.timedelta(days=1), end=today + datetime.timedelta(days=20))
        self.theirs.sites.add(other)
        self.url = reverse('gallery:site_show_list', kwargs={'site_slug': self.site.slug})

    def test_lists_only_that_sites_shows(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn('Mine Now', body)
        self.assertNotIn('Theirs', body)
        self.assertIn('Shows at 120710', body)

    def test_keeps_the_controls_the_global_list_has(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn('ss-status-btn', body)          # slideshow
        staff = User.objects.create_user(
            username='sst@example.com', email='sst@example.com', password='pw')
        add_staff_role(staff)
        self.client.force_login(staff)
        self.assertIn('>New</a>', self.client.get(self.url).content.decode())

    def test_offers_the_submit_action_for_an_open_call(self):
        today = datetime.date.today()
        show = Show.objects.create(
            name='Open Studio', status=Show.STATUS_OPEN_CALL, submission_type='open',
            start=today + datetime.timedelta(days=60), end=today + datetime.timedelta(days=90))
        show.sites.add(self.site)
        self.assertIn(reverse('gallery:artwork_submit', kwargs={'slug': show.slug}),
                      self.client.get(self.url).content.decode())

    def test_unknown_site_is_404(self):
        self.assertEqual(self.client.get('/site/no-such/shows/').status_code, 404)

    def test_nav_shows_link_is_site_scoped_when_a_site_is_current(self):
        """Shows in the nav used to go to the site's detail page, because no
        site-scoped list existed. Artists and Artworks already scoped this way."""
        def nav_shows(path):
            body = self.client.get(path, follow=True).content.decode()
            m = re.search(r'href="([^"]*)">Shows</a>', body)
            return m.group(1) if m else None

        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            import importlib
            from eatart import context_processors
            importlib.reload(context_processors)
            try:
                # Pinned to a site: even the home page's nav scopes to it.
                self.assertEqual(nav_shows('/'), self.url)
            finally:
                importlib.reload(context_processors)

        # On a site's own pages, scoped regardless of the pin.
        self.assertEqual(nav_shows(self.site.get_absolute_url()), self.url)


class CuratorOrderingTests(TestCase):
    """Curators read in last-name order wherever a show lists them."""

    def test_ordered_by_last_name_not_account_age(self):
        today = datetime.date.today()
        show = Show.objects.create(name='Ordered', start=today, end=today,
                                   status=Show.STATUS_PUBLISHED)
        for first, last in [('Zoe', 'Adams'), ('Al', 'Zimmer'), ('Bea', 'Mendez')]:
            show.curators.add(Artist.objects.create(
                name='%s %s' % (first, last), first_name=first, last_name=last,
                email='%s@example.com' % first.lower()))
        self.assertEqual([str(c) for c in show.ordered_curators],
                         ['Zoe Adams', 'Bea Mendez', 'Al Zimmer'])
        # Artist.Meta.ordering is by creation date, so the raw relation differs.
        self.assertNotEqual([str(c) for c in show.curators.all()],
                            [str(c) for c in show.ordered_curators])

    def test_show_page_lists_them_in_that_order(self):
        today = datetime.date.today()
        show = Show.objects.create(name='Ordered Page', start=today, end=today,
                                   status=Show.STATUS_PUBLISHED)
        for first, last in [('Zoe', 'Adams'), ('Al', 'Zimmer')]:
            show.curators.add(Artist.objects.create(
                name='%s %s' % (first, last), first_name=first, last_name=last,
                email='%s@example.com' % first.lower()))
        body = self.client.get(show.get_absolute_url()).content.decode()
        self.assertLess(body.index('Zoe Adams'), body.index('Al Zimmer'))


class HomePageSubmitEntryTests(TestCase):
    """The home page is where most people arrive; it has to offer a way in."""

    def setUp(self):
        today = datetime.date.today()
        self.open_show = Show.objects.create(
            name='Open Studio', status=Show.STATUS_OPEN_CALL, submission_type='open',
            start=today + datetime.timedelta(days=60), end=today + datetime.timedelta(days=90))
        self.past = Show.objects.create(
            name='Past Thing', status=Show.STATUS_CLOSED,
            start=today - datetime.timedelta(days=300), end=today - datetime.timedelta(days=280))
        self.submit_url = reverse('gallery:artwork_submit',
                                  kwargs={'slug': self.open_show.slug})

    def test_anonymous_visitor_is_offered_a_way_in(self):
        body = self.client.get('/').content.decode()
        self.assertIn('>Submit<', body)
        self.assertIn(self.submit_url, body)

    def test_no_action_offered_for_a_show_that_is_closed(self):
        body = self.client.get('/').content.decode()
        self.assertNotIn(reverse('gallery:artwork_submit', kwargs={'slug': self.past.slug}),
                         body)

    def test_action_tracks_the_signed_in_artist_state(self):
        user = User.objects.create_user(
            username='home@example.com', email='home@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='Home', last_name='Body',
                                       email='home@example.com')
        self.client.force_login(user)
        # The label no longer changes; what is still true is that the home page offers
        # the action and says what is outstanding.
        home = self.client.get('/').content.decode()
        self.assertIn('Submit', home)
        self.assertIn('photo', home)

        artist.zipcode = '94710'
        artist.image = _test_jpg('home.jpg')
        artist.save()
        body = self.client.get('/').content.decode()
        self.assertIn('Submit', body)
        self.assertIn(self.submit_url, body)

    def test_show_list_offers_the_same_action(self):
        """One helper drives every surface, so they cannot drift apart."""
        body = self.client.get(reverse('gallery:show_list')).content.decode()
        self.assertIn(self.submit_url, body)

    def test_invitation_only_shows_offer_nothing_to_outsiders(self):
        """Telling a stranger to submit to a show they cannot enter is worse than
        saying nothing."""
        today = datetime.date.today()
        invited_show = Show.objects.create(
            name='Working Craft', status=Show.STATUS_OPEN_CALL, submission_type='invited',
            start=today + datetime.timedelta(days=60), end=today + datetime.timedelta(days=90))
        invited_url = reverse('gallery:artwork_submit', kwargs={'slug': invited_show.slug})

        self.assertNotIn(invited_url, self.client.get('/').content.decode())

        user = User.objects.create_user(
            username='inv@example.com', email='inv@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='In', last_name='Vited',
                                       email='inv@example.com', zipcode='94710',
                                       image=_test_jpg('inv.jpg'))
        self.client.force_login(user)
        self.assertNotIn(invited_url, self.client.get('/').content.decode())

        from gallery.models import ShowInvitation
        ShowInvitation.objects.create(show=invited_show, email='inv@example.com',
                                      artist=artist)
        self.assertIn(invited_url, self.client.get('/').content.decode())


class ArtistPageSubmitEntryTests(TestCase):
    """An incomplete profile no longer becomes homework on the artist's own page."""

    def setUp(self):
        today = datetime.date.today()
        self.show = Show.objects.create(
            name='Open Studio', status=Show.STATUS_OPEN_CALL, submission_type='open',
            start=today + datetime.timedelta(days=60), end=today + datetime.timedelta(days=90))
        self.user = User.objects.create_user(
            username='own@example.com', email='own@example.com', password='pw')
        self.artist = Artist.objects.create(
            user=self.user, first_name='Sam', last_name='Ready',
            email='own@example.com', zipcode='94710')     # deliberately no photo
        self.client.force_login(self.user)

    def test_no_standing_nag_to_complete_the_profile(self):
        body = self.client.get(self.artist.get_absolute_url(), follow=True).content.decode()
        self.assertNotIn('profile is missing', body)

    def test_submit_is_offered_even_with_an_incomplete_profile(self):
        """It used to be hidden, leaving shows listed that could not be acted on.

        The button says Submit and goes to the submit page whatever is still outstanding —
        the view sends them to finish the profile and brings them back. Asserted as
        "listed, and actionable", which is the part that has to stay true however the
        intermediate routing is arranged."""
        import re
        submit_url = reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        body = self.client.get(self.artist.get_absolute_url(), follow=True).content.decode()
        self.assertIn('Shows Accepting Submissions', body)
        href = re.search(r'<a class="card__link new-button" href="([^"]+)"', body)
        self.assertIsNotNone(href, 'the show was listed with no way to act on it')
        self.assertEqual(href.group(1).replace('&amp;', '&'), submit_url)

    def test_following_it_asks_for_the_photo_and_comes_back(self):
        submit_url = reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        r = self.client.get(submit_url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('image', r.headers['Location'])
        self.assertIn('next=', r.headers['Location'])
        body = self.client.get(r.headers['Location']).content.decode()
        self.assertIn('Your details', body)             # the step tracker
        self.assertIn('straight back to submitting', body)

        r = self.client.post(
            reverse('gallery:artist_edit', kwargs={'pk': self.artist.pk}),
            {'first_name': 'Sam', 'last_name': 'Ready', 'email': 'own@example.com',
             'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA', 'next': submit_url, 'image': _test_jpg('own.jpg')})
        self.assertEqual(r.headers['Location'], submit_url)


class ArtworkFormPricingTests(TestCase):
    """Pricing must be chosen, and replacement cost is optional detail."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='pr@example.com', email='pr@example.com', password='pw')

    def test_new_artwork_has_no_preselected_pricing(self):
        """The model defaults to Price on Request, so the form used to arrive with
        that already chosen — an unanswered question that looks like an answer."""
        from gallery.forms import ArtworkForm
        form = ArtworkForm(user=self.user)
        self.assertIn('', [c[0] for c in form.fields['pricing_type'].choices])
        self.assertIsNone(form.fields['pricing_type'].initial)
        self.assertTrue(form.fields['pricing_type'].required)

    def test_submitting_without_choosing_pricing_is_an_error(self):
        from gallery.forms import ArtworkForm
        form = ArtworkForm(data={'name': 'X', 'end_year': 2025, 'medium': 'oil',
                                 'width_inches': 10, 'height_inches': 10},
                           user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('pricing_type', form.errors)

    def test_editing_keeps_the_saved_pricing_and_offers_no_blank(self):
        from gallery.forms import ArtworkForm
        aw = Artwork.objects.create(name='Existing', end_year=2024,
                                    pricing_type=Artwork.PRICING_BEST_OFFER)
        form = ArtworkForm(instance=aw, user=self.user)
        self.assertNotIn('', [c[0] for c in form.fields['pricing_type'].choices])
        self.assertEqual(form.instance.pricing_type, Artwork.PRICING_BEST_OFFER)

    def test_replacement_cost_is_optional_detail_not_pricing(self):
        from gallery.forms import ArtworkForm
        form = ArtworkForm(user=self.user)
        self.assertFalse(form.fields['replacement_cost'].required)
        def legend_holding(field_name):
            for section in form.helper.layout.fields:
                legend = getattr(section, 'legend', None)
                if legend and field_name in _flatten(section):
                    return legend
            return None

        def _flatten(node):
            out = []
            for child in getattr(node, 'fields', []) or []:
                if isinstance(child, str):
                    out.append(child)
                else:
                    out.extend(_flatten(child))
            return out

        self.assertEqual(legend_holding('replacement_cost'),
                         'Additional details (optional)')
        self.assertEqual(legend_holding('pricing_type'), 'Pricing')


class StatusBarNameTests(TestCase):
    """The signed-in artist's name follows the page title — except where that would
    simply repeat it."""

    def setUp(self):
        today = datetime.date.today()
        self.show = Show.objects.create(name='Pub Show', status=Show.STATUS_PUBLISHED,
                                        start=today, end=today)

    def _make(self, tag):
        user = User.objects.create_user(
            username='%s@example.com' % tag, email='%s@example.com' % tag, password='pw')
        artist = Artist.objects.create(user=user, first_name=tag.title(),
                                       last_name='Person', email='%s@example.com' % tag)
        art = Artwork.objects.create(name='%s work' % tag, end_year=2025)
        art.artists.add(artist)
        art.shows.add(self.show)
        return user, artist

    def _status_bar(self, url):
        body = self.client.get(url, follow=True).content.decode()
        return ' '.join(re.search(r'<div id="status-bar">(.*?)</div>',
                                  body, re.S).group(1).split())

    def test_own_profile_does_not_repeat_your_name(self):
        user, artist = self._make('sam')
        self.client.force_login(user)
        bar = self._status_bar(artist.get_absolute_url())
        self.assertIn('Sam Person', bar)
        self.assertEqual(bar.count('Sam Person'), 1)
        self.assertNotIn('status-sep', bar)

    def test_other_pages_still_show_who_you_are(self):
        user, _artist = self._make('sam')
        _other_user, other = self._make('other')
        self.client.force_login(user)

        bar = self._status_bar(other.get_absolute_url())
        self.assertIn('Other Person', bar)
        self.assertIn('Sam Person', bar)

        bar = self._status_bar(self.show.get_absolute_url())
        self.assertIn('Pub Show', bar)
        self.assertIn('Sam Person', bar)


class MakeTestArtistCommandTests(TestCase):
    """Dev helper for entering the submission flow at a chosen step."""

    def _run(self, **opts):
        from io import StringIO
        out = StringIO()
        with override_settings(LOCAL_DEV=True):
            call_command('make_test_artist', stdout=out, **opts)
        return out.getvalue()

    def test_refuses_to_run_outside_local_development(self):
        """It sets a known password and marks an email verified without sending any —
        that must never be possible against a real deployment."""
        from django.core.management.base import CommandError
        with override_settings(LOCAL_DEV=False):
            with self.assertRaises(CommandError):
                call_command('make_test_artist')
        self.assertFalse(User.objects.filter(email='test-artist@example.com').exists())

    def test_states_produce_the_intended_gaps(self):
        expected = {'new-signup': (False, ''), 'no-photo': (False, '94710'),
                    'complete': (True, '94710')}
        for state, (has_photo, zipcode) in expected.items():
            with self.subTest(state=state):
                self._run(state=state, reset=True)
                artist = Artist.objects.get(email='test-artist@example.com')
                self.assertEqual(bool(artist.image), has_photo)
                self.assertEqual(artist.zipcode, zipcode)

    def test_account_is_usable_without_touching_the_console(self):
        self._run(state='complete', reset=True)
        self.assertTrue(self.client.login(
            username='test-artist@example.com', password='testpass123'))
        from allauth.account.models import EmailAddress
        self.assertTrue(EmailAddress.objects.get(
            email='test-artist@example.com').verified)

    def test_no_account_state_creates_nothing(self):
        out = self._run(state='no-account', reset=True)
        self.assertFalse(User.objects.filter(email='test-artist@example.com').exists())
        self.assertIn('No account created', out)

    def test_rerunning_without_reset_is_refused(self):
        from django.core.management.base import CommandError
        self._run(state='complete', reset=True)
        with self.assertRaises(CommandError):
            self._run(state='complete')


class ArtistVenmoVisibilityTests(TestCase):
    """Venmo is payment info, not a public profile field: only the artist, curators
    and gallery admins may see it — the same gate as phone and email."""

    VENMO = '@artist-a-venmo'

    def setUp(self):
        self.owner = User.objects.create_user(
            username='payee-a@example.com', email='payee-a@example.com', password='pw')
        self.artist = Artist.objects.create(
            user=self.owner, first_name='Paula', last_name='Payee',
            email='payee-a@example.com', venmo=self.VENMO)
        # Public visibility: an artwork in a published show.
        show = Show.objects.create(
            name='Venmo Show', slug='venmo-show', status=Show.STATUS_PUBLISHED,
            start=datetime.date.today(), end=datetime.date.today())
        art = Artwork.objects.create(name='W', end_year=2025)
        art.artists.add(self.artist)
        art.shows.add(show)
        self.url = self.artist.get_absolute_url()

    def _body(self, user=None):
        if user:
            self.client.force_login(user)
        r = self.client.get(self.url, follow=True)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_hidden_from_anonymous_public(self):
        self.assertNotIn(self.VENMO, self._body())

    def test_hidden_from_an_unrelated_signed_in_artist(self):
        other = User.objects.create_user(
            username='payee-b@example.com', email='payee-b@example.com', password='pw')
        Artist.objects.create(user=other, first_name='Other', last_name='Artist')
        self.assertNotIn(self.VENMO, self._body(other))

    def test_visible_to_the_artist_themselves(self):
        self.assertIn(self.VENMO, self._body(self.owner))

    def test_visible_to_a_curator(self):
        cur_user = User.objects.create_user(
            username='payee-c@example.com', email='payee-c@example.com', password='pw')
        cur = Artist.objects.create(user=cur_user, first_name='Cur', last_name='Ator')
        Show.objects.create(name='Curated', slug='curated').curators.add(cur)
        self.assertIn(self.VENMO, self._body(cur_user))

    def test_visible_to_staff(self):
        staff = User.objects.create_user(
            username='payee-s@example.com', email='payee-s@example.com', password='pw')
        add_staff_role(staff)
        self.assertIn(self.VENMO, self._body(staff))

    def test_contact_details_hidden_from_public_page(self):
        """Phone and email use the same gate as Venmo, in the rendered page."""
        self.artist.phone = '555-0100'
        self.artist.save(update_fields=['phone'])
        body = self._body()
        self.assertNotIn('555-0100', body)
        self.assertNotIn('payee-a@example.com', body)

    def test_contact_details_visible_to_curator_and_owner(self):
        self.artist.phone = '555-0100'
        self.artist.save(update_fields=['phone'])
        owner_body = self._body(self.owner)
        self.assertIn('555-0100', owner_body)
        self.assertIn('payee-a@example.com', owner_body)

    def test_contact_details_never_in_structured_data(self):
        """JSON-LD is written for crawlers, so anything in it is public regardless of
        who is signed in. artist_to_schema also feeds artwork/show/event schemas and
        the /api/schema/artists feed, so a leak here is a bulk leak."""
        self.artist.phone = '555-0100'
        self.artist.save(update_fields=['phone'])
        for user in (None, self.owner):
            body = self._body(user)
            for block in re.findall(
                    r'<script type="application/ld\+json">(.*?)</script>', body, re.S):
                self.assertNotIn('555-0100', block)
                self.assertNotIn('payee-a@example.com', block)

    def test_gallery_own_contact_details_are_still_published(self):
        """Only per-artist contact is withheld; the gallery's own stays public."""
        from django.test import RequestFactory
        from eatart.schemaorg.mappers import gallery_to_schema, schema_to_dict
        gal = schema_to_dict(gallery_to_schema(RequestFactory().get('/')))
        self.assertTrue(gal.get('email'))
        self.assertTrue(gal.get('telephone'))

    def test_not_emitted_in_public_structured_data(self):
        """The JSON-LD block is built by artist_to_schema and bypasses template
        gating entirely, so assert the handle is absent from it separately."""
        body = self._body()
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            body, re.S)
        self.assertTrue(blocks, 'expected a JSON-LD block on the artist page')
        for b in blocks:
            self.assertNotIn(self.VENMO, b)
            self.assertNotIn(self.VENMO.lstrip('@'), b)


class ArtistDeletePermissionTests(MediaImageMixin, TestCase):
    """Artists with artworks in shows cannot be deleted except by staff."""

    def setUp(self):
        self._setup_media()
        self.staff_user = User.objects.create_user(
            username='staff@example.com', email='staff@example.com', password='pw'
        )
        add_staff_role(self.staff_user)

        # Artist whose artworks are NOT in any show
        self.free_user = User.objects.create_user(
            username='free@example.com', email='free@example.com', password='pw'
        )
        self.free_artist = Artist.objects.create(
            user=self.free_user, name='Free Artist',
            first_name='Free', last_name='Artist', email='free@example.com', phone='',
            image=self.TEST_ARTIST_IMAGE,
        )
        self.free_artwork = Artwork.objects.create(
            name='Free Artwork', created_by=self.free_user, end_year=2024,
        )
        self.free_artwork.artists.add(self.free_artist)

        # Artist whose artworks ARE in a show
        self.shown_user = User.objects.create_user(
            username='shown@example.com', email='shown@example.com', password='pw'
        )
        self.shown_artist = Artist.objects.create(
            user=self.shown_user, name='Shown Artist',
            first_name='Shown', last_name='Artist', email='shown@example.com', phone='',
            image=self.TEST_ARTIST_IMAGE,
        )
        today = datetime.date.today()
        self.show = Show.objects.create(
            name='Test Show', start=today, end=today + datetime.timedelta(days=7),
            status=Show.STATUS_PUBLISHED,
        )
        self.shown_artwork = Artwork.objects.create(
            name='Shown Artwork', created_by=self.shown_user, end_year=2024,
        )
        self.shown_artwork.artists.add(self.shown_artist)
        self.shown_artwork.shows.add(self.show)

    def tearDown(self):
        self._teardown_media()

    def test_artist_can_delete_themselves_when_no_artworks_in_shows(self):
        self.client.force_login(self.free_user)
        response = self.client.post(reverse('gallery:artist_delete', kwargs={'pk': self.free_artist.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Artist.objects.filter(pk=self.free_artist.pk).exists())

    def test_artist_cannot_delete_themselves_when_artworks_in_show(self):
        self.client.force_login(self.shown_user)
        response = self.client.post(reverse('gallery:artist_delete', kwargs={'pk': self.shown_artist.pk}))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Artist.objects.filter(pk=self.shown_artist.pk).exists())

    def test_staff_can_delete_artist_with_artworks_in_show(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('gallery:artist_delete', kwargs={'pk': self.shown_artist.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Artist.objects.filter(pk=self.shown_artist.pk).exists())

    def test_other_artist_cannot_delete_unrelated_artist(self):
        self.client.force_login(self.free_user)
        response = self.client.post(reverse('gallery:artist_delete', kwargs={'pk': self.shown_artist.pk}))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Artist.objects.filter(pk=self.shown_artist.pk).exists())

    def test_delete_button_hidden_on_artist_detail_when_artworks_in_show(self):
        self.client.force_login(self.shown_user)
        response = self.client.get(self.shown_artist.get_absolute_url())
        self.assertContains(response, 'Edit')
        self.assertNotContains(response, reverse('gallery:artist_delete', kwargs={'pk': self.shown_artist.pk}))

    def test_delete_button_visible_on_artist_detail_when_no_artworks_in_shows(self):
        self.client.force_login(self.free_user)
        response = self.client.get(self.free_artist.get_absolute_url())
        self.assertContains(response, reverse('gallery:artist_delete', kwargs={'pk': self.free_artist.pk}))

    def test_delete_button_visible_on_artist_detail_for_staff_even_with_shown_artworks(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(self.shown_artist.get_absolute_url())
        self.assertContains(response, reverse('gallery:artist_delete', kwargs={'pk': self.shown_artist.pk}))


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class CuratorScopedPermissionTests(TestCase):
    """Curators should only have elevated access to shows they are explicitly assigned to."""

    def setUp(self):
        # Curator A assigned only to own_show
        self.curator_user = User.objects.create_user(
            username='curator@example.com', email='curator@example.com', password='pw'
        )
        self.curator_artist = Artist.objects.create(
            user=self.curator_user, name='Curator One',
            first_name='Curator', last_name='One', email='curator@example.com', phone='',
        )

        # Curator B assigned only to other_show
        self.other_curator_user = User.objects.create_user(
            username='other@example.com', email='other@example.com', password='pw'
        )
        self.other_curator_artist = Artist.objects.create(
            user=self.other_curator_user, name='Curator Two',
            first_name='Curator', last_name='Two', email='other@example.com', phone='',
        )

        # Artist with artworks in each show and one private artwork
        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='pw'
        )
        self.artist = Artist.objects.create(
            user=self.artist_user, name='Test Artist',
            first_name='Test', last_name='Artist', email='artist@example.com', phone='',
        )

        today = datetime.date.today()
        # own_show is in draft — not publicly visible
        self.own_show = Show.objects.create(
            name='Own Show', start=today, end=today + datetime.timedelta(days=7),
            status=Show.STATUS_DRAFT,
        )
        self.own_show.curators.add(self.curator_artist)

        # other_show is in draft — managed by a different curator
        self.other_show = Show.objects.create(
            name='Other Show', start=today, end=today + datetime.timedelta(days=7),
            status=Show.STATUS_DRAFT,
        )
        self.other_show.curators.add(self.other_curator_artist)

        # Artwork visible only via own_show
        self.own_artwork = Artwork.objects.create(
            name='Own Show Artwork', created_by=self.artist_user, end_year=2024,
        )
        self.own_artwork.artists.add(self.artist)
        self.own_artwork.shows.add(self.own_show)

        # Artwork visible only via other_show
        self.other_artwork = Artwork.objects.create(
            name='Other Show Artwork', created_by=self.artist_user, end_year=2024,
        )
        self.other_artwork.artists.add(self.artist)
        self.other_artwork.shows.add(self.other_show)

        # Artwork in no show at all (private)
        self.private_artwork = Artwork.objects.create(
            name='Private Artwork', created_by=self.artist_user, end_year=2024,
        )
        self.private_artwork.artists.add(self.artist)

    # --- Show list visibility ---

    def test_curator_sees_own_unpublished_show_in_list(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:show_list'))
        self.assertContains(response, 'Own Show')

    def test_curator_does_not_see_other_unpublished_show_in_list(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:show_list'))
        self.assertNotContains(response, 'Other Show')

    def test_curator_cannot_access_detail_of_other_unpublished_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(self.other_show.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_curator_can_access_detail_of_own_unpublished_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(self.own_show.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    # --- Artwork visibility ---

    def test_curator_sees_artworks_in_own_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertContains(response, 'Own Show Artwork')

    def test_curator_does_not_see_artworks_only_in_other_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertNotContains(response, 'Other Show Artwork')

    def test_curator_does_not_see_private_artworks_by_other_artists(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertNotContains(response, 'Private Artwork')

    def test_curator_cannot_access_detail_of_artwork_in_other_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(self.other_artwork.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    # --- Artist visibility ---

    def test_curator_sees_artists_who_have_work_in_own_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:artist_list'))
        self.assertContains(response, 'Test Artist')

    # --- Show management gates ---

    def test_curator_cannot_edit_show_they_do_not_curate(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:show_edit', kwargs={'pk': self.other_show.pk}))
        self.assertEqual(response.status_code, 403)

    def test_curator_can_edit_show_they_curate(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:show_edit', kwargs={'pk': self.own_show.pk}))
        self.assertEqual(response.status_code, 200)

    def test_curator_cannot_delete_show_they_do_not_curate(self):
        self.client.force_login(self.curator_user)
        response = self.client.post(reverse('gallery:show_delete', kwargs={'pk': self.other_show.pk}))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Show.objects.filter(pk=self.other_show.pk).exists())

    # --- Artwork edit access ---

    def test_curator_can_edit_artwork_in_own_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:artwork_edit', kwargs={'pk': self.own_artwork.pk}))
        self.assertEqual(response.status_code, 200)

    def test_curator_cannot_edit_artwork_in_other_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:artwork_edit', kwargs={'pk': self.other_artwork.pk}))
        self.assertEqual(response.status_code, 403)

    def test_curator_cannot_edit_private_artwork_by_other_artist(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:artwork_edit', kwargs={'pk': self.private_artwork.pk}))
        self.assertEqual(response.status_code, 403)

    # --- Show delete access ---

    def test_curator_cannot_delete_own_show(self):
        self.client.force_login(self.curator_user)
        response = self.client.post(reverse('gallery:show_delete', kwargs={'pk': self.own_show.pk}))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Show.objects.filter(pk=self.own_show.pk).exists())

    def test_superuser_can_delete_show(self):
        superuser = User.objects.create_superuser(
            username='admin@example.com', email='admin@example.com', password='pw'
        )
        self.client.force_login(superuser)
        response = self.client.post(reverse('gallery:show_delete', kwargs={'pk': self.own_show.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Show.objects.filter(pk=self.own_show.pk).exists())


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class OpenCallFlowTests(MediaImageMixin, TestCase):
    """End-to-end tests for the open call submission, jury review, and promotion flow."""

    def setUp(self):
        self._setup_media()
        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='pw'
        )
        self.artist = Artist.objects.create(
            user=self.artist_user,
            name='Frida Kahlo',
            first_name='Frida',
            last_name='Kahlo',
            email='artist@example.com',
            phone='',
            zipcode='94103',
            image=self.TEST_ARTIST_IMAGE,
        )

        self.curator_user = User.objects.create_user(
            username='curator@example.com', email='curator@example.com', password='pw'
        )
        self.curator_artist = Artist.objects.create(
            user=self.curator_user,
            name='Marcel Duchamp',
            first_name='Marcel',
            last_name='Duchamp',
            email='curator@example.com',
            phone='',
        )

        self.show = Show.objects.create(
            name='Open Call Spring 2026',
            start=datetime.date.today() + datetime.timedelta(days=30),
            end=datetime.date.today() + datetime.timedelta(days=60),
            submission_type=Show.SUBMISSION_OPEN,
            submission_deadline=datetime.date.today() + datetime.timedelta(days=7),
            status=Show.STATUS_OPEN_CALL,
        )
        self.show.curators.add(self.curator_artist)

        self.artwork = Artwork.objects.create(
            name='Still Life with Sunflowers',
            created_by=self.artist_user,
            end_year=2026,
        )
        self.artwork.artists.add(self.artist)

    def tearDown(self):
        self._teardown_media()

    # --- Model property tests ---

    def test_show_is_accepting_submissions_within_deadline(self):
        self.assertTrue(self.show.is_accepting_submissions)

    def test_show_is_not_accepting_submissions_when_in_review(self):
        self.show.status = Show.STATUS_IN_REVIEW
        self.show.save(update_fields=['status'])
        self.assertFalse(self.show.is_accepting_submissions)

    def test_show_open_call_phase_is_open_before_deadline(self):
        self.assertEqual(self.show.open_call_phase, 'open')

    def test_show_open_call_phase_is_jury_when_in_review(self):
        self.show.status = Show.STATUS_IN_REVIEW
        self.show.save(update_fields=['status'])
        self.assertEqual(self.show.open_call_phase, 'jury')

    def test_show_not_in_open_call_or_review_status_has_no_phase(self):
        for status in (Show.STATUS_UNDER_CONSIDERATION, Show.STATUS_DRAFT,
                       Show.STATUS_PUBLISHED, Show.STATUS_CLOSED):
            with self.subTest(status=status):
                self.show.status = status
                self.assertIsNone(self.show.open_call_phase)

    # --- Submission flow ---

    def test_artist_can_submit_artwork_to_open_call_show(self):
        self.client.force_login(self.artist_user)
        submit_url = reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})

        get_response = self.client.get(submit_url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(submit_url, {
            'artwork': self.artwork.pk,
            'statement': 'My artist statement.',
        }, follow=True)
        self.assertEqual(post_response.status_code, 200)
        self.assertTrue(
            ArtworkSubmission.objects.filter(show=self.show, artwork=self.artwork).exists()
        )

    def test_submission_limit_blocks_when_reached(self):
        self.show.max_submissions_per_artist = 1
        self.show.save(update_fields=['max_submissions_per_artist'])
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user)
        extra = Artwork.objects.create(name='Extra Piece', created_by=self.artist_user, end_year=2026)
        extra.artists.add(self.artist)
        self.client.force_login(self.artist_user)
        self.client.post(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug}),
            {'artwork': extra.pk}, follow=True)
        self.assertFalse(ArtworkSubmission.objects.filter(show=self.show, artwork=extra).exists())

    def test_submission_allowed_under_limit(self):
        self.show.max_submissions_per_artist = 3
        self.show.save(update_fields=['max_submissions_per_artist'])
        self.client.force_login(self.artist_user)
        self.client.post(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug}),
            {'artwork': self.artwork.pk}, follow=True)
        self.assertTrue(ArtworkSubmission.objects.filter(show=self.show, artwork=self.artwork).exists())

    def test_no_limit_allows_unlimited(self):
        self.assertIsNone(self.show.max_submissions_per_artist)  # blank by default
        self.client.force_login(self.artist_user)
        self.client.post(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug}),
            {'artwork': self.artwork.pk}, follow=True)
        self.assertTrue(ArtworkSubmission.objects.filter(show=self.show, artwork=self.artwork).exists())

    def test_submission_has_submitted_status_by_default(self):
        self.client.force_login(self.artist_user)
        self.client.post(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug}),
            {'artwork': self.artwork.pk, 'statement': ''},
        )
        sub = ArtworkSubmission.objects.get(show=self.show, artwork=self.artwork)
        self.assertEqual(sub.status, ArtworkSubmission.SUBMITTED)

    def test_artist_cannot_submit_same_artwork_twice(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.artist_user)
        response = self.client.get(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        )
        # Already-submitted artwork should not appear in the form choices
        self.assertNotContains(response, self.artwork.name)

    def test_duplicate_post_is_handled_gracefully_not_500(self):
        # Simulates a race condition or JS-bypass: the first POST succeeds,
        # the second POST arrives with the same artwork before the page reloads.
        # The DB unique_together constraint would fire; the view must catch it.
        self.client.force_login(self.artist_user)
        submit_url = reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        # First submission goes through normally.
        self.client.post(submit_url, {'artwork': self.artwork.pk})
        self.assertTrue(ArtworkSubmission.objects.filter(show=self.show, artwork=self.artwork).exists())
        # Second POST with the same artwork must NOT raise a 500.
        response = self.client.post(submit_url, {'artwork': self.artwork.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        # Still only one submission in the database.
        self.assertEqual(
            ArtworkSubmission.objects.filter(show=self.show, artwork=self.artwork).count(), 1
        )

    def test_submission_blocked_when_status_is_in_review(self):
        self.show.status = Show.STATUS_IN_REVIEW
        self.show.save(update_fields=['status'])
        self.client.force_login(self.artist_user)

        response = self.client.get(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        )
        # Status is In Review — no longer accepting, should redirect away
        self.assertEqual(response.status_code, 302)

    def test_unauthenticated_user_cannot_submit(self):
        response = self.client.get(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.headers['Location'])

    def test_user_without_artist_profile_cannot_submit(self):
        no_profile_user = User.objects.create_user(
            username='noprofile@example.com', email='noprofile@example.com', password='pw'
        )
        self.client.force_login(no_profile_user)

        response = self.client.get(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        )
        # No artist profile → redirect away
        self.assertEqual(response.status_code, 302)

    def test_incomplete_profile_redirects_to_artist_edit_with_highlight_params(self):
        # Missing zipcode → bounce to the editor, highlighting it and carrying a
        # ?next= back here so finishing the profile resumes the submission.
        incomplete_user = User.objects.create_user(
            username='incomplete@example.com', email='incomplete@example.com', password='pw'
        )
        Artist.objects.create(
            user=incomplete_user,
            first_name='Ada',
            last_name='Lovelace',
            email='incomplete@example.com',
            # No image, no zipcode
        )
        self.client.force_login(incomplete_user)
        submit_url = reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        response = self.client.get(submit_url)
        self.assertEqual(response.status_code, 302)
        location = response.headers['Location']
        self.assertIn('artist', location)
        self.assertIn('highlight=', location)
        self.assertIn('zipcode', location)
        self.assertIn('next=', location)
        self.assertIn('image', location)
        # first_name and last_name are present so should NOT be in highlight
        self.assertNotIn('first_name', location)
        self.assertNotIn('last_name', location)

    def test_complete_profile_is_not_redirected(self):
        # Artist with all required fields filled in should reach the submit page
        self.client.force_login(self.artist_user)
        response = self.client.get(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 200)

    # --- Submissions review (curator view) ---

    def test_curator_can_view_show_submissions(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.curator_user)

        response = self.client.get(
            reverse('gallery:show_submissions', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.artwork.name)

    def test_artist_cannot_view_show_submissions(self):
        self.client.force_login(self.artist_user)

        response = self.client.get(
            reverse('gallery:show_submissions', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 404)

    def test_curator_can_select_submission(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.curator_user)

        response = self.client.post(
            reverse('gallery:update_submission_status', kwargs={'pk': sub.pk}),
            {'decision': ArtworkSubmission.CURATOR_SELECTED},
        )
        self.assertEqual(response.status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.curator_decision, ArtworkSubmission.CURATOR_SELECTED)
        self.assertEqual(sub.status, ArtworkSubmission.SUBMITTED)

    def test_curator_can_reject_submission(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:update_submission_status', kwargs={'pk': sub.pk}),
            {'decision': ArtworkSubmission.CURATOR_REJECTED},
        )
        sub.refresh_from_db()
        self.assertEqual(sub.curator_decision, ArtworkSubmission.CURATOR_REJECTED)
        self.assertEqual(sub.status, ArtworkSubmission.SUBMITTED)

    def test_curator_decision_not_visible_to_submitting_artist(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.curator_user)
        self.client.post(
            reverse('gallery:update_submission_status', kwargs={'pk': sub.pk}),
            {'decision': ArtworkSubmission.CURATOR_SELECTED},
        )
        sub.refresh_from_db()
        self.assertEqual(sub.curator_decision, ArtworkSubmission.CURATOR_SELECTED)

        self.client.force_login(self.artist_user)
        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Selected')
        self.assertNotContains(response, 'Rejected')

    def test_artist_sees_own_pending_submission_on_show_detail(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.artist_user)

        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.artwork.name)

    # --- Promote artworks ---

    def test_curator_can_view_promote_page(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)

        response = self.client.get(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.artwork.name)

    def test_promote_adds_selected_artworks_and_artists_to_show(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )

        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())

    def _publish_show(self):
        """Helper: POST to show_edit to set status to published."""
        self.client.post(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}), {
            'name': self.show.name,
            'show_type': self.show.show_type,
            'start': self.show.start,
            'end': self.show.end,
            'status': Show.STATUS_PUBLISHED,
            'submission_type': Show.SUBMISSION_OPEN,
            'submission_scope': Show.SCOPE_LOCAL,
            'submission_deadline': self.show.submission_deadline,
            'curators': [self.curator_artist.pk],
            'tags': [],
        })

    def test_promote_does_not_send_emails(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )

        self.assertEqual(len(mail.outbox), 0)

    def test_publish_sends_acceptance_email_to_artist(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)
        self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))

        self._publish_show()

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.artist_user.email, mail.outbox[0].recipients())
        self.assertIn(self.show.name, mail.outbox[0].subject)

    def test_publish_sends_rejection_email_for_rejected_submissions(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_REJECTED,
        )
        second_artwork = Artwork.objects.create(
            name='Second Piece', created_by=self.artist_user, end_year=2026
        )
        second_artwork.artists.add(self.artist)
        ArtworkSubmission.objects.create(
            show=self.show, artwork=second_artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)
        self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))

        self._publish_show()

        self.assertEqual(len(mail.outbox), 2)

    def test_publish_does_not_resend_emails_if_already_published(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)
        self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))
        self._publish_show()
        self.assertEqual(len(mail.outbox), 1)

        # Publishing again (e.g. editing name while published) should not resend
        self._publish_show()
        self.assertEqual(len(mail.outbox), 1)

    def test_promote_does_not_add_rejected_artworks_to_show(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_REJECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )

        self.assertFalse(self.show.artworks.filter(pk=self.artwork.pk).exists())

    def test_artist_cannot_access_promote_page(self):
        self.client.force_login(self.artist_user)

        response = self.client.get(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 404)

    # --- Status-driven visibility ---

    def test_artwork_not_visible_to_public_when_show_not_published(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.save(update_fields=['status'])
        self.artwork.shows.add(self.show)

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertNotContains(response, self.artwork.name)

    def test_artwork_visible_to_public_when_show_is_published(self):
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        self.artwork.shows.add(self.show)

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertContains(response, self.artwork.name)

    def test_artwork_visible_to_public_when_show_is_closed(self):
        self.show.status = Show.STATUS_CLOSED
        self.show.save(update_fields=['status'])
        self.artwork.shows.add(self.show)

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertContains(response, self.artwork.name)

    def test_artist_can_view_own_artwork_regardless_of_show_status(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.save(update_fields=['status'])
        self.artwork.shows.add(self.show)
        self.client.force_login(self.artist_user)

        response = self.client.get(self.artwork.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_curator_can_view_all_artworks_regardless_of_show_status(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.save(update_fields=['status'])
        self.artwork.shows.add(self.show)
        self.client.force_login(self.curator_user)

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertContains(response, self.artwork.name)

    def test_submitted_artwork_not_visible_to_public_during_open_call(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.save(update_fields=['status'])
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertNotContains(response, self.artwork.name)

    def test_submitted_artwork_not_visible_to_public_during_in_review(self):
        self.show.status = Show.STATUS_IN_REVIEW
        self.show.save(update_fields=['status'])
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertNotContains(response, self.artwork.name)

    def test_submitted_artwork_not_visible_to_public_in_draft_after_promote(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)
        self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        self.client.logout()

        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())
        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertNotContains(response, self.artwork.name)

    def test_artist_can_retract_submission_while_open(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.artist_user)

        self.client.post(reverse('gallery:retract_submission', kwargs={'pk': sub.pk}))

        self.assertFalse(ArtworkSubmission.objects.filter(pk=sub.pk).exists())

    def test_artist_cannot_retract_submission_after_deadline_closed(self):
        self.show.status = Show.STATUS_IN_REVIEW
        self.show.save(update_fields=['status'])
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.artist_user)

        response = self.client.post(reverse('gallery:retract_submission', kwargs={'pk': sub.pk}))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(ArtworkSubmission.objects.filter(pk=sub.pk).exists())

    # --- Promote in Draft state auto-publishes ---

    def test_promote_in_draft_state_publishes_show_without_sending_emails(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))

        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_PUBLISHED)
        # Emails are now sent separately via send_selection_emails, not inline
        self.assertEqual(len(mail.outbox), 0)

    def test_send_selection_emails_sends_and_stamps_email_sent_at(self):
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
            status=ArtworkSubmission.ACCEPTED,
        )
        self.assertIsNone(sub.email_sent_at)
        self.client.force_login(self.curator_user)

        # Thread runs synchronously in test environment; join briefly
        import threading
        sent = []
        orig_start = threading.Thread.start
        def sync_start(self_thread):
            self_thread.run()
            sent.append(True)
        with self.settings():
            threading.Thread.start = sync_start
            try:
                self.client.post(reverse('gallery:send_selection_emails', kwargs={'slug': self.show.slug}))
            finally:
                threading.Thread.start = orig_start

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.artist_user.email, mail.outbox[0].recipients())
        sub.refresh_from_db()
        self.assertIsNotNone(sub.email_sent_at)

    def test_send_selection_emails_skips_already_sent(self):
        from django.utils import timezone
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
            status=ArtworkSubmission.ACCEPTED,
            email_sent_at=timezone.now(),
        )
        self.client.force_login(self.curator_user)

        import threading
        orig_start = threading.Thread.start
        threading.Thread.start = lambda self_thread: self_thread.run()
        try:
            self.client.post(reverse('gallery:send_selection_emails', kwargs={'slug': self.show.slug}))
        finally:
            threading.Thread.start = orig_start

        self.assertEqual(len(mail.outbox), 0)

    def test_promote_in_open_call_state_does_not_auto_publish(self):
        # show starts as STATUS_OPEN_CALL in setUp
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))

        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_OPEN_CALL)
        self.assertEqual(len(mail.outbox), 0)

    # --- Complete end-to-end flow ---

    def test_full_open_call_flow(self):
        """Submit → select → promote → publish → artwork in show, email sent."""
        # 1. Artist submits artwork
        self.client.force_login(self.artist_user)
        self.client.post(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug}),
            {'artwork': self.artwork.pk, 'statement': 'Statement for the piece.'},
        )
        sub = ArtworkSubmission.objects.get(show=self.show, artwork=self.artwork)
        self.assertEqual(sub.status, ArtworkSubmission.SUBMITTED)

        # 2. Curator marks submission as selected (curator_decision only — not visible to artist yet)
        self.client.force_login(self.curator_user)
        self.client.post(
            reverse('gallery:update_submission_status', kwargs={'pk': sub.pk}),
            {'decision': ArtworkSubmission.CURATOR_SELECTED},
        )
        sub.refresh_from_db()
        self.assertEqual(sub.curator_decision, ArtworkSubmission.CURATOR_SELECTED)
        self.assertEqual(sub.status, ArtworkSubmission.SUBMITTED)

        # confirmation email sent on submit
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.artist_user.email, mail.outbox[0].recipients())

        # 3. Curator promotes — adds artwork/artist to show, no additional emails
        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )
        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())
        self.assertEqual(len(mail.outbox), 1)

        # 4. Curator publishes — acceptance email sent
        self._publish_show()
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(self.artist_user.email, mail.outbox[1].recipients())


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class ShowStatusTests(TestCase):
    """Tests for Show status state machine: transitions, visibility, and access control."""

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff@example.com', email='staff@example.com', password='pw'
        )
        add_staff_role(self.staff_user)

        self.curator_user = User.objects.create_user(
            username='curator@example.com', email='curator@example.com', password='pw'
        )
        self.curator_artist = Artist.objects.create(
            user=self.curator_user,
            name='Curator One',
            first_name='Curator',
            last_name='One',
            email='curator@example.com',
            phone='',
        )

        self.juror_user = User.objects.create_user(
            username='juror@example.com', email='juror@example.com', password='pw'
        )

        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='pw'
        )

        self.show = Show.objects.create(
            name='Test Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        self.show.curators.add(self.curator_artist)

        from reviews.models import ShowJuror
        ShowJuror.objects.create(show=self.show, user=self.juror_user)

    # --- Default and initial state ---

    def test_default_status_is_under_consideration(self):
        show = Show.objects.create(name='New Show', start=datetime.date.today(), end=datetime.date.today())
        self.assertEqual(show.status, Show.STATUS_UNDER_CONSIDERATION)

    # --- transition_to() ---

    def test_valid_transitions_succeed(self):
        transitions = [
            (Show.STATUS_UNDER_CONSIDERATION, Show.STATUS_OPEN_CALL),
            (Show.STATUS_OPEN_CALL, Show.STATUS_IN_REVIEW),
            (Show.STATUS_IN_REVIEW, Show.STATUS_DRAFT),
            (Show.STATUS_DRAFT, Show.STATUS_PUBLISHED),
            (Show.STATUS_PUBLISHED, Show.STATUS_CLOSED),
        ]
        show = Show.objects.create(name='Transition Show', start=datetime.date.today(), end=datetime.date.today())
        for from_status, to_status in transitions:
            with self.subTest(from_status=from_status, to_status=to_status):
                show.status = from_status
                show.save(update_fields=['status'])
                show.transition_to(to_status)
                show.refresh_from_db()
                self.assertEqual(show.status, to_status)

    def test_invalid_transition_raises_value_error(self):
        invalid = [
            (Show.STATUS_UNDER_CONSIDERATION, Show.STATUS_PUBLISHED),
            (Show.STATUS_UNDER_CONSIDERATION, Show.STATUS_DRAFT),
            (Show.STATUS_OPEN_CALL, Show.STATUS_DRAFT),
            (Show.STATUS_IN_REVIEW, Show.STATUS_OPEN_CALL),
            (Show.STATUS_PUBLISHED, Show.STATUS_DRAFT),
            (Show.STATUS_CLOSED, Show.STATUS_PUBLISHED),
        ]
        show = Show.objects.create(name='Invalid Show', start=datetime.date.today(), end=datetime.date.today())
        for from_status, to_status in invalid:
            with self.subTest(from_status=from_status, to_status=to_status):
                show.status = from_status
                show.save(update_fields=['status'])
                with self.assertRaises(ValueError):
                    show.transition_to(to_status)

    def test_under_consideration_transitions_only_to_open_call(self):
        show = Show.objects.create(name='Draft Show', start=datetime.date.today(), end=datetime.date.today())
        self.assertEqual(show.get_valid_transitions()[Show.STATUS_UNDER_CONSIDERATION], [Show.STATUS_OPEN_CALL])
        with self.assertRaises(ValueError):
            show.transition_to(Show.STATUS_DRAFT)

    # --- is_accepting_submissions ---

    def test_accepting_submissions_when_open_call_status_and_within_deadline(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.submission_deadline = datetime.date.today() + datetime.timedelta(days=3)
        self.show.save(update_fields=['status', 'submission_deadline'])
        self.assertTrue(self.show.is_accepting_submissions)

    def test_not_accepting_when_status_is_not_open_call(self):
        self.show.submission_deadline = datetime.date.today() + datetime.timedelta(days=3)
        for status in (Show.STATUS_UNDER_CONSIDERATION, Show.STATUS_IN_REVIEW,
                       Show.STATUS_DRAFT, Show.STATUS_PUBLISHED, Show.STATUS_CLOSED):
            with self.subTest(status=status):
                self.show.status = status
                self.assertFalse(self.show.is_accepting_submissions)

    def test_accepting_when_open_call_status_regardless_of_deadline(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.submission_deadline = datetime.date.today() - datetime.timedelta(days=1)
        self.show.save(update_fields=['status', 'submission_deadline'])
        # Deadline is informational only — only status controls acceptance
        self.assertTrue(self.show.is_accepting_submissions)

    # --- open_call_phase ---

    def test_open_call_phase_is_open_when_status_is_open_call(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.assertEqual(self.show.open_call_phase, 'open')

    def test_open_call_phase_is_jury_when_status_is_in_review(self):
        self.show.status = Show.STATUS_IN_REVIEW
        self.assertEqual(self.show.open_call_phase, 'jury')

    def test_open_call_phase_is_none_for_non_call_statuses(self):
        for status in (Show.STATUS_UNDER_CONSIDERATION, Show.STATUS_DRAFT,
                       Show.STATUS_PUBLISHED, Show.STATUS_CLOSED):
            with self.subTest(status=status):
                self.show.status = status
                self.assertIsNone(self.show.open_call_phase)

    # --- Show list visibility ---

    def test_public_user_only_sees_public_status_shows(self):
        for status in Show.PUBLIC_STATUSES:
            Show.objects.create(
                name=f'Public {status}', start=datetime.date.today(),
                end=datetime.date.today(), status=status,
            )

        response = self.client.get(reverse('gallery:show_list'))
        self.assertEqual(response.status_code, 200)

        for show in response.context['object_list']:
            self.assertIn(show.status, Show.PUBLIC_STATUSES)

    def test_public_user_does_not_see_under_consideration_shows(self):
        private = Show.objects.create(
            name='Private Show', start=datetime.date.today(),
            end=datetime.date.today(), status=Show.STATUS_UNDER_CONSIDERATION,
        )
        response = self.client.get(reverse('gallery:show_list'))
        ids = [s.id for s in response.context['object_list']]
        self.assertNotIn(private.id, ids)

    def test_public_user_does_not_see_draft_shows(self):
        draft = Show.objects.create(
            name='Draft Show', start=datetime.date.today(),
            end=datetime.date.today(), status=Show.STATUS_DRAFT,
        )
        response = self.client.get(reverse('gallery:show_list'))
        ids = [s.id for s in response.context['object_list']]
        self.assertNotIn(draft.id, ids)

    def test_staff_sees_all_shows_regardless_of_status(self):
        Show.objects.create(
            name='Hidden Show', start=datetime.date.today(),
            end=datetime.date.today(), status=Show.STATUS_UNDER_CONSIDERATION,
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('gallery:show_list'))
        statuses = {s.status for s in response.context['object_list']}
        self.assertIn(Show.STATUS_UNDER_CONSIDERATION, statuses)

    def test_curator_sees_all_shows_regardless_of_status(self):
        Show.objects.create(
            name='Hidden Show', start=datetime.date.today(),
            end=datetime.date.today(), status=Show.STATUS_UNDER_CONSIDERATION,
        )
        self.client.force_login(self.curator_user)
        response = self.client.get(reverse('gallery:show_list'))
        statuses = {s.status for s in response.context['object_list']}
        self.assertIn(Show.STATUS_UNDER_CONSIDERATION, statuses)

    def test_juror_sees_all_shows_regardless_of_status(self):
        Show.objects.create(
            name='Hidden Show', start=datetime.date.today(),
            end=datetime.date.today(), status=Show.STATUS_UNDER_CONSIDERATION,
        )
        self.client.force_login(self.juror_user)
        response = self.client.get(reverse('gallery:show_list'))
        statuses = {s.status for s in response.context['object_list']}
        self.assertIn(Show.STATUS_UNDER_CONSIDERATION, statuses)

    # --- Show detail visibility ---

    def test_public_user_gets_404_for_under_consideration_show(self):
        self.show.status = Show.STATUS_UNDER_CONSIDERATION
        self.show.save(update_fields=['status'])
        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_public_user_gets_404_for_draft_show(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_public_user_can_view_all_public_status_shows(self):
        for status in Show.PUBLIC_STATUSES:
            with self.subTest(status=status):
                self.show.status = status
                self.show.save(update_fields=['status'])
                response = self.client.get(self.show.get_absolute_url())
                self.assertEqual(response.status_code, 200)

    def test_staff_can_view_under_consideration_show(self):
        self.show.status = Show.STATUS_UNDER_CONSIDERATION
        self.show.save(update_fields=['status'])
        self.client.force_login(self.staff_user)
        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_curator_can_view_draft_show(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        self.client.force_login(self.curator_user)
        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_juror_can_view_under_consideration_show(self):
        self.show.status = Show.STATUS_UNDER_CONSIDERATION
        self.show.save(update_fields=['status'])
        self.client.force_login(self.juror_user)
        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_regular_artist_cannot_view_draft_show(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        self.client.force_login(self.artist_user)
        response = self.client.get(self.show.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    # --- Index page ---

    def test_index_hides_non_public_shows_for_anonymous_users(self):
        private = Show.objects.create(
            name='Private Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=1),
            status=Show.STATUS_UNDER_CONSIDERATION,
        )
        response = self.client.get(reverse('index'))
        all_shows = (
            list(response.context['current_shows'])
            + list(response.context['future_shows'])
            + list(response.context['past_shows'])
        )
        ids = [s.id for s in all_shows]
        self.assertNotIn(private.id, ids)

    def test_index_shows_non_public_shows_for_staff(self):
        private = Show.objects.create(
            name='Private Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=1),
            status=Show.STATUS_UNDER_CONSIDERATION,
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('index'))
        all_shows = (
            list(response.context['current_shows'])
            + list(response.context['future_shows'])
            + list(response.context['past_shows'])
        )
        ids = [s.id for s in all_shows]
        self.assertIn(private.id, ids)

    # --- transition_show_status view ---

    def test_curator_can_transition_status(self):
        self.show.status = Show.STATUS_UNDER_CONSIDERATION
        self.show.save(update_fields=['status'])
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:transition_show_status', kwargs={'pk': self.show.pk}),
            {'status': Show.STATUS_OPEN_CALL},
        )

        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_OPEN_CALL)

    def test_invalid_transition_rejected_by_view(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.save(update_fields=['status'])
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:transition_show_status', kwargs={'pk': self.show.pk}),
            {'status': Show.STATUS_PUBLISHED},  # not a valid transition from OPEN_CALL
        )

        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_OPEN_CALL)

    def test_artist_cannot_transition_status(self):
        self.show.status = Show.STATUS_UNDER_CONSIDERATION
        self.show.save(update_fields=['status'])
        self.client.force_login(self.artist_user)

        response = self.client.post(
            reverse('gallery:transition_show_status', kwargs={'pk': self.show.pk}),
            {'status': Show.STATUS_OPEN_CALL},
        )

        self.assertEqual(response.status_code, 404)
        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_UNDER_CONSIDERATION)

    def test_transition_to_in_review_sends_juror_emails(self):
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.submission_type = Show.SUBMISSION_OPEN
        self.show.save(update_fields=['status', 'submission_type'])
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:transition_show_status', kwargs={'pk': self.show.pk}),
            {'status': Show.STATUS_IN_REVIEW},
        )

        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_IN_REVIEW)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.juror_user.email, mail.outbox[0].recipients())

    def test_draft_to_published_redirects_to_promote(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        self.client.force_login(self.curator_user)

        response = self.client.post(
            reverse('gallery:transition_show_status', kwargs={'pk': self.show.pk}),
            {'status': Show.STATUS_PUBLISHED},
        )

        self.assertRedirects(
            response,
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}),
            fetch_redirect_response=False,
        )
        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_DRAFT)  # not changed yet


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class SubmittableShowsTests(MediaImageMixin, TestCase):
    """Tests for the submittable_shows context on the artist detail page."""

    def setUp(self):
        self._setup_media()
        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='pw'
        )
        self.artist = Artist.objects.create(
            user=self.artist_user, name='Test Artist',
            first_name='Test', last_name='Artist',
            email='artist@example.com', phone='',
            image=self.TEST_ARTIST_IMAGE,
        )
        self.open_show = Show.objects.create(
            name='Open Call Show',
            start=datetime.date.today(), end=datetime.date.today(),
            status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_OPEN,
        )
        self.invited_show = Show.objects.create(
            name='Invited Show',
            start=datetime.date.today(), end=datetime.date.today(),
            status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_INVITED,
        )

    def tearDown(self):
        self._teardown_media()

    def test_open_call_show_appears_in_submittable_shows(self):
        self.client.force_login(self.artist_user)
        response = self.client.get(self.artist.get_absolute_url())
        self.assertIn(self.open_show, response.context['submittable_shows'])

    def test_invited_show_excluded_without_invitation(self):
        self.client.force_login(self.artist_user)
        response = self.client.get(self.artist.get_absolute_url())
        self.assertNotIn(self.invited_show, response.context['submittable_shows'])

    def test_invited_show_included_with_invitation(self):
        from gallery.models.exhibitions import ShowInvitation
        ShowInvitation.objects.create(
            show=self.invited_show, email='artist@example.com', artist=self.artist,
            invited_by=self.artist_user,
        )
        self.client.force_login(self.artist_user)
        response = self.client.get(self.artist.get_absolute_url())
        self.assertIn(self.invited_show, response.context['submittable_shows'])

    def test_invited_show_included_when_invited_by_different_email(self):
        # Curator invited the artist at a contact email that differs from their
        # login email; the invitation is linked to the artist. It must still show.
        from gallery.models.exhibitions import ShowInvitation
        ShowInvitation.objects.create(
            show=self.invited_show, email='different-contact@example.com',
            artist=self.artist, invited_by=self.artist_user,
        )
        self.client.force_login(self.artist_user)
        response = self.client.get(self.artist.get_absolute_url())
        self.assertIn(self.invited_show, response.context['submittable_shows'])

    def test_submittable_shows_not_in_context_for_other_artist_page(self):
        other_user = User.objects.create_user(
            username='other@example.com', email='other@example.com', password='pw'
        )
        other_artist = Artist.objects.create(
            user=other_user, name='Other Artist',
            first_name='Other', last_name='Artist',
            email='other@example.com', phone='',
        )
        self.client.force_login(self.artist_user)
        response = self.client.get(other_artist.get_absolute_url())
        self.assertNotIn('submittable_shows', response.context)

    def test_non_open_call_show_excluded(self):
        draft_show = Show.objects.create(
            name='Draft Show', start=datetime.date.today(), end=datetime.date.today(),
            status=Show.STATUS_DRAFT, submission_type=Show.SUBMISSION_OPEN,
        )
        self.client.force_login(self.artist_user)
        response = self.client.get(self.artist.get_absolute_url())
        self.assertNotIn(draft_show, response.context['submittable_shows'])


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class ArtworkCreateAutoAssignTests(TestCase):
    """Tests for automatic artist assignment and created_by on artwork creation."""

    def setUp(self):
        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='pw'
        )
        self.artist = Artist.objects.create(
            user=self.artist_user, name='Test Artist',
            first_name='Test', last_name='Artist',
            email='artist@example.com', phone='',
        )
        artist_group, _ = Group.objects.get_or_create(name='artist')
        self.artist_user.groups.add(artist_group)

    def _minimal_image(self):
        # 1x1 red pixel GIF — smallest valid image file
        gif = (
            b'GIF87a\x01\x00\x01\x00\x80\x00\x00\xff\x00\x00\xff\xff\xff'
            b'!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01'
            b'\x00\x00\x02\x02D\x01\x00;'
        )
        return SimpleUploadedFile('test.gif', gif, content_type='image/gif')

    def test_artist_auto_assigned_on_create(self):
        self.client.force_login(self.artist_user)
        response = self.client.post(
            reverse('gallery:artwork_new'),
            {
                'name': 'New Piece',
                'end_year': 2026,
                'medium': 'oil on canvas',
                'width_inches': '10',
                'height_inches': '12',
                'image': self._minimal_image(),
                'pricing_type': 'on_request',
            },
        )
        artwork = Artwork.objects.filter(name='New Piece').first()
        self.assertIsNotNone(artwork)
        self.assertIn(self.artist, artwork.artists.all())
        self.assertEqual(artwork.created_by, self.artist_user)

    def test_artists_field_hidden_for_user_with_linked_artist(self):
        self.client.force_login(self.artist_user)
        response = self.client.get(reverse('gallery:artwork_new'))
        self.assertNotIn('artists', response.context['form'].fields)


class AddArtworkOnBehalfTests(MediaImageMixin, TestCase):
    """A curator/admin can add an artwork on behalf of a managed artist to an
    invitation-only show, with no account/email/submission from the artist."""

    def setUp(self):
        self._setup_media()
        self.staff = User.objects.create_user(
            username='boss@example.com', email='boss@example.com', password='pw', is_staff=True)
        # A managed artist with NO linked user and NO email — the proxy case.
        self.managed = Artist.objects.create(
            name='Alice Nomail', first_name='Alice', last_name='Nomail',
            email='', zipcode='94103', image=self.TEST_ARTIST_IMAGE)
        self.artwork = Artwork.objects.create(name='Blue Study', end_year=2026,
                                              width_inches=10, height_inches=8)
        self.artwork.artists.add(self.managed)
        self.show = Show.objects.create(
            name='Invited Only', submission_type=Show.SUBMISSION_INVITED,
            status=Show.STATUS_PUBLISHED,
            start=datetime.date.today() + datetime.timedelta(days=30),
            end=datetime.date.today() + datetime.timedelta(days=60))
        self.url = reverse('gallery:add_artwork_on_behalf', kwargs={'slug': self.show.slug})

    def tearDown(self):
        self._teardown_media()

    def _minimal_image(self):
        gif = (b'GIF87a\x01\x00\x01\x00\x80\x00\x00\xff\x00\x00\xff\xff\xff'
               b'!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01'
               b'\x00\x00\x02\x02D\x01\x00;')
        return SimpleUploadedFile('test.gif', gif, content_type='image/gif')

    def test_the_form_renders_without_asking_crispy_for_a_missing_field(self):
        """The page looked right and logged a traceback on every render.

        The view popped `artists` off the form after __init__ had already built the
        crispy layout naming it, so crispy failed to resolve the field, logged the
        failure and silently dropped it. Output correct, logs full of KeyErrors, and one
        crispy version away from a 500 — so this asserts on the log, which is the only
        place the fault was visible."""
        import logging
        self.client.force_login(self.staff)
        with self.assertLogs(level='WARNING') as captured:
            logging.getLogger().warning('sentinel')   # assertLogs needs at least one
            response = self.client.get(self.url, {'artist': self.managed.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('artists', response.context['new_form'].fields)
        self.assertFalse([line for line in captured.output if 'artists' in line],
                         'crispy could not resolve a field the layout still names')

    def test_add_existing_artwork_on_behalf(self):
        self.client.force_login(self.staff)
        r = self.client.post(self.url, {
            'artist': self.managed.pk, 'artwork': self.artwork.pk, 'action': 'add_existing'})
        self.assertRedirects(r, reverse('gallery:show_submissions', kwargs={'slug': self.show.slug}))
        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())
        sub = ArtworkSubmission.objects.get(show=self.show, artwork=self.artwork)
        self.assertEqual(sub.curator_decision, ArtworkSubmission.CURATOR_SELECTED)
        self.assertEqual(sub.submitted_by, self.staff)

    def test_create_new_artwork_on_behalf(self):
        self.client.force_login(self.staff)
        r = self.client.post(self.url, {
            'artist': self.managed.pk, 'action': 'create_new',
            'name': 'Fresh Piece', 'medium': 'oil', 'end_year': 2026,
            'width_inches': 12, 'height_inches': 9, 'pricing_type': 'nfs',
            'image': self._minimal_image()})
        self.assertRedirects(r, reverse('gallery:show_submissions', kwargs={'slug': self.show.slug}))
        aw = Artwork.objects.get(name='Fresh Piece')
        self.assertIn(self.managed, aw.artists.all())          # attributed to the chosen artist
        self.assertEqual(aw.created_by, self.staff)
        self.assertTrue(self.show.artworks.filter(pk=aw.pk).exists())

    def test_non_manager_cannot_add(self):
        nobody = User.objects.create_user(username='x@e.com', email='x@e.com', password='pw')
        self.client.force_login(nobody)
        r = self.client.post(self.url, {
            'artist': self.managed.pk, 'artwork': self.artwork.pk, 'action': 'add_existing'})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(self.show.artworks.filter(pk=self.artwork.pk).exists())


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class InviteArtistsEditTests(TestCase):
    """Curators can correct the email on an existing invitation."""

    def setUp(self):
        self.curator_user = User.objects.create_user(
            username='cur@example.com', email='cur@example.com', password='pw', is_staff=True)
        self.curator = Artist.objects.create(
            user=self.curator_user, name='Cur', first_name='Cur', last_name='Ator', email='cur@example.com')
        self.show = Show.objects.create(
            name='Invite Edit Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
            status=Show.STATUS_OPEN_CALL, submission_type=Show.SUBMISSION_INVITED)
        self.show.curators.add(self.curator)
        from gallery.models.exhibitions import ShowInvitation
        self.inv = ShowInvitation.objects.create(
            show=self.show, email='wrong@example.com', invited_by=self.curator_user)
        self.client.force_login(self.curator_user)

    def _url(self):
        return reverse('gallery:invite_artists', kwargs={'slug': self.show.slug})

    def test_edit_updates_invitation_email(self):
        from gallery.models.exhibitions import ShowInvitation
        r = self.client.post(self._url(), {
            'action': 'edit', 'invitation_pk': self.inv.pk, 'email': 'Right@Example.com'})
        self.assertEqual(r.status_code, 302)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.email, 'right@example.com')

    def test_edit_rejects_duplicate_email(self):
        from gallery.models.exhibitions import ShowInvitation
        ShowInvitation.objects.create(show=self.show, email='taken@example.com', invited_by=self.curator_user)
        self.client.post(self._url(), {
            'action': 'edit', 'invitation_pk': self.inv.pk, 'email': 'taken@example.com'})
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.email, 'wrong@example.com')   # unchanged

    def test_bulk_add_only_emails_not_yet_sent(self):
        from django.core import mail
        from django.utils import timezone
        from gallery.models.exhibitions import ShowInvitation
        # existing invitation already emailed → must not be re-sent
        self.inv.email_sent_at = timezone.now()
        self.inv.save(update_fields=['email_sent_at'])
        mail.outbox = []
        self.client.post(self._url(), {'emails': 'wrong@example.com\nfresh@example.com'})
        # only the new address was emailed
        recipients = [addr for m in mail.outbox for addr in m.to]
        self.assertIn('fresh@example.com', recipients)
        self.assertNotIn('wrong@example.com', recipients)
        self.assertIsNotNone(ShowInvitation.objects.get(show=self.show, email='fresh@example.com').email_sent_at)

    def test_resend_action_emails_again(self):
        from django.core import mail
        from django.utils import timezone
        self.inv.email_sent_at = timezone.now()
        self.inv.save(update_fields=['email_sent_at'])
        mail.outbox = []
        self.client.post(self._url(), {'action': 'resend', 'invitation_pk': self.inv.pk})
        self.assertIn('wrong@example.com', [addr for m in mail.outbox for addr in m.to])

    def test_edit_relinks_artist(self):
        target = Artist.objects.create(
            user=User.objects.create_user(username='real@example.com', email='real@example.com', password='pw'),
            name='Real', first_name='Real', last_name='Artist', email='real@example.com')
        self.client.post(self._url(), {
            'action': 'edit', 'invitation_pk': self.inv.pk, 'email': 'real@example.com'})
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.artist_id, target.id)


class ArtworkInquireTests(TestCase):
    """The Inquire form is open to anonymous users, guarded by the time-trap."""

    def setUp(self):
        self.artist = Artist.objects.create(
            name='Contact Artist', first_name='C', last_name='A', email='artist@example.com')
        self.artwork = Artwork.objects.create(name='For Sale Piece', end_year=2025)
        self.artwork.artists.add(self.artist)
        self.show = Show.objects.create(
            name='Public Show', status=Show.STATUS_PUBLISHED,
            start=datetime.date.today() - datetime.timedelta(days=1),
            end=datetime.date.today() + datetime.timedelta(days=30))
        self.show.artworks.add(self.artwork)

    def _url(self):
        return reverse('gallery:artwork_inquire', kwargs={'pk': self.artwork.pk})

    def test_anonymous_can_open_inquiry_page(self):
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)   # no login redirect

    def test_valid_anonymous_inquiry_is_accepted(self):
        from django.core import signing
        import time
        r = self.client.post(self._url(), {
            'sender_name': 'Jane', 'sender_email': 'jane@example.com',
            'message': 'I am interested in this piece.',
            'address': '',                              # honeypot must be empty
            'ts': signing.dumps(time.time() - 10),      # form loaded 10s ago
        })
        self.assertEqual(r.status_code, 302)            # success → redirect to the artwork
        self.assertEqual(r['Location'], self.artwork.get_absolute_url())

    def test_too_fast_submission_is_rejected(self):
        from django.core import signing
        import time
        r = self.client.post(self._url(), {
            'sender_name': 'Bot', 'sender_email': 'bot@example.com',
            'message': 'spam', 'address': '',
            'ts': signing.dumps(time.time()),           # submitted instantly
        })
        self.assertEqual(r.status_code, 200)            # re-rendered, not sent
        self.assertContains(r, 'too quickly')

    def test_missing_time_trap_token_is_rejected(self):
        r = self.client.post(self._url(), {
            'sender_name': 'Bot', 'sender_email': 'bot@example.com',
            'message': 'spam', 'address': '',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'too quickly')


class AcceptInvitationTests(TestCase):
    """Claiming an invitation via its token link binds it to the current account,
    even when that account uses a different email than the invitation."""

    def setUp(self):
        self.show = Show.objects.create(
            name='Token Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
            status=Show.STATUS_OPEN_CALL, submission_type=Show.SUBMISSION_INVITED)
        from gallery.models.exhibitions import ShowInvitation
        self.inv = ShowInvitation.objects.create(show=self.show, email='invited@one.com')
        # artist signed up with a DIFFERENT email than the invitation
        self.user = User.objects.create_user(
            username='signed@up.com', email='signed@up.com', password='pw')
        self.artist = Artist.objects.create(
            user=self.user, name='Signed Up', first_name='Signed', last_name='Up',
            email='signed@up.com')

    def _url(self):
        return reverse('gallery:accept_invitation',
                       kwargs={'slug': self.show.slug, 'token': self.inv.token})

    def test_anonymous_is_redirected_to_login(self):
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])

    def test_claim_binds_invitation_and_grants_access(self):
        from gallery.permissions import user_invited_to_show
        # before claiming, the mismatched email is not recognized
        self.assertFalse(user_invited_to_show(self.show, self.user))
        self.client.force_login(self.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 302)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.claimed_by_id, self.user.id)
        self.assertEqual(self.inv.artist_id, self.artist.id)
        self.assertTrue(user_invited_to_show(self.show, self.user))

    def test_bad_token_404s(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse('gallery:accept_invitation',
                                    kwargs={'slug': self.show.slug, 'token': 'nope'}))
        self.assertEqual(r.status_code, 404)


class InvitedShowDisplayTests(TestCase):
    """Tests that invited shows suppress Open Call label and deadline for non-invited users."""

    def setUp(self):
        self.invited_show = Show.objects.create(
            name='Secret Show',
            start=datetime.date.today(), end=datetime.date.today(),
            status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_INVITED,
            submission_deadline=datetime.date.today() + datetime.timedelta(days=7),
        )

    def test_deadline_hidden_for_anonymous_user_on_invited_show(self):
        response = self.client.get(self.invited_show.get_absolute_url())
        self.assertNotContains(response, 'Submission deadline')

    def test_invited_show_submissions_context_has_correct_invited_total(self):
        curator_user = User.objects.create_user(
            username='curator@example.com', email='curator@example.com', password='pw',
            is_staff=True,
        )
        curator_artist = Artist.objects.create(
            user=curator_user, name='Curator', first_name='Curator', last_name='One',
            email='curator@example.com', phone='',
        )
        self.invited_show.curators.add(curator_artist)
        from gallery.models.exhibitions import ShowInvitation
        ShowInvitation.objects.create(
            show=self.invited_show, email='a@example.com', invited_by=curator_user,
        )
        ShowInvitation.objects.create(
            show=self.invited_show, email='b@example.com', invited_by=curator_user,
        )
        self.client.force_login(curator_user)
        response = self.client.get(
            reverse('gallery:show_submissions', kwargs={'slug': self.invited_show.slug})
        )
        self.assertEqual(response.context['invited_total'], 2)


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class PlacardTests(TestCase):
    """Tests for ShowArtworkNumber assignment, placard HTML/JSON endpoints, and renumber."""

    def setUp(self):
        self.curator_user = User.objects.create_user(
            username='curator@example.com', email='curator@example.com', password='pw'
        )
        self.curator_artist = Artist.objects.create(
            user=self.curator_user, name='Curator', first_name='Curator', last_name='C', email='curator@example.com', phone='',
        )
        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='pw'
        )
        self.artist = Artist.objects.create(
            user=self.artist_user, name='Artist A', first_name='Artist', last_name='A', email='artist@example.com', phone='',
        )
        self.show = Show.objects.create(
            name='Placard Test Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=30),
            submission_type=Show.SUBMISSION_OPEN,
            status=Show.STATUS_OPEN_CALL,
        )
        self.show.curators.add(self.curator_artist)
        self.artwork1 = Artwork.objects.create(name='Artwork One', created_by=self.artist_user, end_year=2025)
        self.artwork1.artists.add(self.artist)
        self.artwork2 = Artwork.objects.create(name='Artwork Two', created_by=self.artist_user, end_year=2025)
        self.artwork2.artists.add(self.artist)

    def _submit_and_select(self, artwork):
        return ArtworkSubmission.objects.create(
            show=self.show, artwork=artwork, submitted_by=self.artist_user,
            curator_decision=ArtworkSubmission.CURATOR_SELECTED,
        )

    def _promote(self):
        self.client.force_login(self.curator_user)
        self.client.post(reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug}))

    # --- Number assignment ---

    def test_promote_assigns_numbers_in_submission_order(self):
        sub1 = self._submit_and_select(self.artwork1)
        sub2 = self._submit_and_select(self.artwork2)
        self._promote()
        n1 = ShowArtworkNumber.objects.get(show=self.show, artwork=self.artwork1)
        n2 = ShowArtworkNumber.objects.get(show=self.show, artwork=self.artwork2)
        self.assertEqual(n1.number, 1)
        self.assertEqual(n2.number, 2)

    def test_promote_does_not_renumber_already_numbered_artworks(self):
        self._submit_and_select(self.artwork1)
        self._promote()
        # Submit and select artwork2 after first promote
        self._submit_and_select(self.artwork2)
        self.show.status = Show.STATUS_OPEN_CALL
        self.show.save(update_fields=['status'])
        self._promote()
        n1 = ShowArtworkNumber.objects.get(show=self.show, artwork=self.artwork1)
        n2 = ShowArtworkNumber.objects.get(show=self.show, artwork=self.artwork2)
        self.assertEqual(n1.number, 1)
        self.assertEqual(n2.number, 2)

    def test_rejected_artworks_do_not_get_numbers(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork1, submitted_by=self.artist_user,
            status=ArtworkSubmission.REJECTED,
        )
        self._promote()
        self.assertFalse(ShowArtworkNumber.objects.filter(show=self.show, artwork=self.artwork1).exists())

    # --- Placard HTML endpoint ---

    def test_placard_html_returns_200_for_valid_number(self):
        self._submit_and_select(self.artwork1)
        self._promote()
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        response = self.client.get(reverse('gallery:placard_html', kwargs={'number': 1}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.artwork1.name)

    def test_placard_html_shows_not_found_for_missing_number(self):
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        response = self.client.get(reverse('gallery:placard_html', kwargs={'number': 99}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No artwork')

    def test_placard_html_accessible_without_login(self):
        self._submit_and_select(self.artwork1)
        self._promote()
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        self.client.logout()
        response = self.client.get(reverse('gallery:placard_html', kwargs={'number': 1}))
        self.assertEqual(response.status_code, 200)

    # --- Placard JSON endpoint ---

    def test_placard_json_returns_artwork_data(self):
        self._submit_and_select(self.artwork1)
        self._promote()
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        response = self.client.get(reverse('gallery:placard_json', kwargs={'number': 1}))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['number'], 1)
        self.assertEqual(data['artwork']['name'], self.artwork1.name)
        self.assertIn(self.artist.name, data['artwork']['artists'])
        self.assertIn('url', data['artwork'])   # artwork page URL for the MagTag QR

    def test_placard_json_ok_for_upcoming_show_artwork_with_image(self):
        # Regression: an artwork WITH an image used to raise AttributeError
        # (bad `card_thumbnail`) -> HTTP 500. Also covers _current_show serving
        # the next not-yet-open published show.
        self._submit_and_select(self.artwork1)
        self._promote()
        # Give it a (truthy) image without triggering imagekit's save-time
        # processing — .update() bypasses save signals. The endpoint's image
        # thumbnail lookup must not 500 even if the file can't be read.
        Artwork.objects.filter(pk=self.artwork1.pk).update(image='artwork_images/x.jpg')
        self.show.status = Show.STATUS_PUBLISHED
        self.show.start = datetime.date.today() + datetime.timedelta(days=2)   # opens soon
        self.show.end = datetime.date.today() + datetime.timedelta(days=32)
        self.show.save(update_fields=['status', 'start', 'end'])
        response = self.client.get(reverse('gallery:placard_json', kwargs={'number': 1}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['artwork']['name'], self.artwork1.name)

    def test_placard_json_for_site_returns_that_sites_show(self):
        from gallery.models import Site
        site = Site.objects.create(name='Venue A', status=Site.STATUS_PUBLISHED)
        self.show.sites.add(site)
        self._submit_and_select(self.artwork1)
        self._promote()
        self.show.status = Show.STATUS_PUBLISHED
        self.show.start = datetime.date.today() + datetime.timedelta(days=2)   # upcoming
        self.show.end = datetime.date.today() + datetime.timedelta(days=32)
        self.show.save(update_fields=['status', 'start', 'end'])
        r = self.client.get(reverse('gallery:placard_json_for_site',
                                    kwargs={'site_slug': site.slug, 'number': 1}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['artwork']['name'], self.artwork1.name)

    def test_placard_json_for_site_is_isolated_per_site(self):
        from gallery.models import Site
        site_a = Site.objects.create(name='Venue A', status=Site.STATUS_PUBLISHED)
        site_b = Site.objects.create(name='Venue B', status=Site.STATUS_PUBLISHED)
        self.show.sites.add(site_a)          # show is only at site A
        self._submit_and_select(self.artwork1)
        self._promote()
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        # Site B has no show → its placard #1 is not found (no cross-site bleed).
        r = self.client.get(reverse('gallery:placard_json_for_site',
                                    kwargs={'site_slug': site_b.slug, 'number': 1}))
        self.assertEqual(r.status_code, 404)

    def test_placard_json_returns_404_for_missing_number(self):
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        response = self.client.get(reverse('gallery:placard_json', kwargs={'number': 99}))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error'], 'not found')

    def test_placard_json_accessible_without_login(self):
        self._submit_and_select(self.artwork1)
        self._promote()
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        self.client.logout()
        response = self.client.get(reverse('gallery:placard_json', kwargs={'number': 1}))
        self.assertEqual(response.status_code, 200)

    # --- Renumber ---

    def test_renumber_reassigns_numbers_from_scratch(self):
        self._submit_and_select(self.artwork1)
        self._submit_and_select(self.artwork2)
        self._promote()
        # Manually flip numbers to simulate out-of-order state
        ShowArtworkNumber.objects.filter(show=self.show, artwork=self.artwork1).update(number=99)
        self.client.force_login(self.curator_user)
        self.client.post(reverse('gallery:renumber_artworks', kwargs={'slug': self.show.slug}))
        numbers = list(ShowArtworkNumber.objects.filter(show=self.show).order_by('number').values_list('number', flat=True))
        self.assertEqual(numbers, [1, 2])

    def test_artist_cannot_renumber(self):
        self._submit_and_select(self.artwork1)
        self._promote()
        self.client.force_login(self.artist_user)
        response = self.client.post(reverse('gallery:renumber_artworks', kwargs={'slug': self.show.slug}))
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_user_cannot_renumber(self):
        self.client.logout()
        response = self.client.post(reverse('gallery:renumber_artworks', kwargs={'slug': self.show.slug}))
        self.assertNotEqual(response.status_code, 200)


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
@override_settings(
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
)
class SiteFeatureTests(TestCase):
    """Tests for the Site model, views, and site-scoped artist/artwork filtering."""

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff@example.com', email='staff@example.com', password='pw'
        )
        self.staff_user.is_staff = True
        self.staff_user.save()

        self.regular_user = User.objects.create_user(
            username='regular@example.com', email='regular@example.com', password='pw'
        )

        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='pw'
        )
        self.artist = Artist.objects.create(
            user=self.artist_user, name='Test Artist', first_name='Test', last_name='Artist',
            email='artist@example.com', zipcode='94710',
        )

        self.published_site = Site.objects.create(
            name='Main Gallery',
            street='1207 Tenth St',
            city='Berkeley',
            state='CA',
            postal_code='94710',
            country='USA',
            status=Site.STATUS_PUBLISHED,
        )
        self.draft_site = Site.objects.create(
            name='Draft Gallery',
            status=Site.STATUS_DRAFT,
        )

        self.show = Show.objects.create(
            name='Test Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=30),
            status=Show.STATUS_PUBLISHED,
        )
        self.show.sites.add(self.published_site)

        self.artwork = Artwork.objects.create(
            name='Test Artwork', end_year=2025, created_by=self.artist_user,
            medium='oil on canvas', width_inches=10, height_inches=12,
        )
        self.artwork.artists.add(self.artist)
        self.artwork.shows.add(self.show)

        # A second show+artwork NOT linked to any site
        self.other_show = Show.objects.create(
            name='Other Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=10),
            status=Show.STATUS_PUBLISHED,
        )
        self.other_artist_user = User.objects.create_user(
            username='other@example.com', email='other@example.com', password='pw'
        )
        self.other_artist = Artist.objects.create(
            name='Other Artist', first_name='Other', last_name='Artist',
            email='other@example.com', zipcode='94720',
        )
        self.other_artwork = Artwork.objects.create(
            name='Other Artwork', end_year=2025, created_by=self.other_artist_user,
            medium='watercolor', width_inches=8, height_inches=10,
        )
        self.other_artwork.artists.add(self.other_artist)
        self.other_artwork.shows.add(self.other_show)

    # ── Model basics ──────────────────────────────────────────────────────────

    def test_site_slug_auto_generated(self):
        site = Site.objects.create(name='My New Gallery')
        self.assertEqual(site.slug, 'my-new-gallery')

    def test_site_get_absolute_url(self):
        self.assertIn(self.published_site.slug, self.published_site.get_absolute_url())
        self.assertTrue(self.published_site.get_absolute_url().startswith('/site/'))

    def test_duplicate_site_names_get_unique_slugs(self):
        site_a = Site.objects.create(name='Duplicate Gallery')
        site_b = Site.objects.create(name='Duplicate Gallery')
        self.assertNotEqual(site_a.slug, site_b.slug)

    # ── Show.sites M2M ────────────────────────────────────────────────────────

    def test_show_can_be_associated_with_site(self):
        self.assertIn(self.published_site, self.show.sites.all())

    def test_site_shows_reverse_relation(self):
        self.assertIn(self.show, self.published_site.shows.all())

    def test_show_form_has_no_location_field(self):
        from gallery.forms import ShowForm
        form = ShowForm(user=self.staff_user)
        self.assertNotIn('location', form.fields)

    def test_show_form_has_sites_field(self):
        from gallery.forms import ShowForm
        form = ShowForm(user=self.staff_user)
        self.assertIn('sites', form.fields)

    # ── Site list view ────────────────────────────────────────────────────────

    def test_site_list_anonymous_sees_only_published(self):
        response = self.client.get(reverse('gallery:site_list'))
        self.assertEqual(response.status_code, 200)
        sites = list(response.context['sites'])
        self.assertIn(self.published_site, sites)
        self.assertNotIn(self.draft_site, sites)

    def test_site_list_staff_sees_draft_and_published(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('gallery:site_list'))
        self.assertEqual(response.status_code, 200)
        sites = list(response.context['sites'])
        self.assertIn(self.published_site, sites)
        self.assertIn(self.draft_site, sites)

    # ── Site detail view ──────────────────────────────────────────────────────

    def test_published_site_detail_returns_200_for_anonymous(self):
        response = self.client.get(self.published_site.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_draft_site_detail_returns_404_for_anonymous(self):
        response = self.client.get(self.draft_site.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_draft_site_detail_returns_200_for_staff(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(self.draft_site.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_site_detail_context_includes_shows(self):
        response = self.client.get(self.published_site.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.show, response.context['shows'])

    def test_site_detail_shows_site_name(self):
        response = self.client.get(self.published_site.get_absolute_url())
        self.assertContains(response, self.published_site.name)

    # ── Site create/edit/delete — staff only ──────────────────────────────────

    def test_anonymous_redirected_from_site_new(self):
        response = self.client.get(reverse('gallery:site_new'))
        self.assertNotEqual(response.status_code, 200)

    def test_non_staff_gets_403_from_site_new(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse('gallery:site_new'))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_get_site_new(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('gallery:site_new'))
        self.assertEqual(response.status_code, 200)

    def test_staff_can_create_site(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('gallery:site_new'), {
            'name': 'New Test Site',
            'street': '123 Main St',
            'city': 'Berkeley',
            'state': 'CA',
            'postal_code': '94710',
            'country': 'US',
            'email': '',
            'phone': '',
            'instagram': '',
            'website': '',
            'description': '',
            'status': Site.STATUS_DRAFT,
            'country': 'US',
            'latitude': '',
            'longitude': '',
            # Room dimensions (RoomConfigMixin form) are required to save.
            'width_in': '384',
            'depth_in': '576',
            'height_in': '120',
            # Obstacle inline formset management form (empty).
            'obstacles-TOTAL_FORMS': '0',
            'obstacles-INITIAL_FORMS': '0',
            'obstacles-MIN_NUM_FORMS': '0',
            'obstacles-MAX_NUM_FORMS': '1000',
            # Support (catalog) inline formset management form (empty).
            'supports-TOTAL_FORMS': '0',
            'supports-INITIAL_FORMS': '0',
            'supports-MIN_NUM_FORMS': '0',
            'supports-MAX_NUM_FORMS': '1000',
        })
        self.assertTrue(Site.objects.filter(name='New Test Site').exists())
        new_site = Site.objects.get(name='New Test Site')
        self.assertRedirects(response, new_site.get_absolute_url())

    def test_site_support_texture_is_saved(self):
        import tempfile, shutil
        from django.test import override_settings
        from gallery.models.room import SiteSupport
        png = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
               b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
               b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
        tmp = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=tmp):
                self.client.force_login(self.staff_user)
                self.client.post(reverse('gallery:site_new'), {
                    'name': 'Texture Site', 'street': '', 'city': '', 'state': '',
                    'postal_code': '', 'country': 'US', 'email': '', 'phone': '',
                    'instagram': '', 'website': '', 'description': '',
                    'status': Site.STATUS_DRAFT, 'latitude': '', 'longitude': '',
                    'width_in': '384', 'depth_in': '576', 'height_in': '120',
                    'obstacles-TOTAL_FORMS': '0', 'obstacles-INITIAL_FORMS': '0',
                    'obstacles-MIN_NUM_FORMS': '0', 'obstacles-MAX_NUM_FORMS': '1000',
                    'supports-TOTAL_FORMS': '1', 'supports-INITIAL_FORMS': '0',
                    'supports-MIN_NUM_FORMS': '0', 'supports-MAX_NUM_FORMS': '1000',
                    'supports-0-label': 'Wood Plinth', 'supports-0-w_in': '16',
                    'supports-0-h_in': '40', 'supports-0-d_in': '16',
                    'supports-0-DELETE': '', 'supports-0-id': '',
                    'supports-0-texture': SimpleUploadedFile('t.png', png, content_type='image/png'),
                })
                ss = SiteSupport.objects.get(label='Wood Plinth', room_config__site__name='Texture Site')
                self.assertTrue(ss.texture)   # the uploaded texture was persisted
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_site_support_can_be_deleted(self):
        from gallery.models.room import RoomConfig, SiteSupport
        cfg = RoomConfig.objects.create(site=self.published_site, width_in=384, depth_in=576, height_in=120)
        a = SiteSupport.objects.create(room_config=cfg, label='Plinth A', w_in=16, h_in=40, d_in=16)
        b = SiteSupport.objects.create(room_config=cfg, label='Plinth B', w_in=20, h_in=44, d_in=20)
        self.client.force_login(self.staff_user)
        self.client.post(reverse('gallery:site_edit', kwargs={'slug': self.published_site.slug}), {
            'name': self.published_site.name, 'street': '', 'city': '', 'state': '',
            'postal_code': '', 'country': 'US', 'email': '', 'phone': '',
            'instagram': '', 'website': '', 'description': '',
            'status': Site.STATUS_PUBLISHED, 'latitude': '', 'longitude': '',
            'width_in': '384', 'depth_in': '576', 'height_in': '120',
            'obstacles-TOTAL_FORMS': '0', 'obstacles-INITIAL_FORMS': '0',
            'obstacles-MIN_NUM_FORMS': '0', 'obstacles-MAX_NUM_FORMS': '1000',
            'supports-TOTAL_FORMS': '2', 'supports-INITIAL_FORMS': '2',
            'supports-MIN_NUM_FORMS': '0', 'supports-MAX_NUM_FORMS': '1000',
            'supports-0-id': str(a.pk), 'supports-0-label': 'Plinth A',
            'supports-0-w_in': '16', 'supports-0-h_in': '40', 'supports-0-d_in': '16',
            'supports-0-DELETE': 'on',
            'supports-1-id': str(b.pk), 'supports-1-label': 'Plinth B',
            'supports-1-w_in': '20', 'supports-1-h_in': '44', 'supports-1-d_in': '20',
            'supports-1-DELETE': 'on',
        })
        self.assertEqual(SiteSupport.objects.filter(room_config=cfg).count(), 0)

    def test_staff_can_edit_site(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('gallery:site_edit', kwargs={'slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)

    def test_non_staff_cannot_edit_site(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse('gallery:site_edit', kwargs={'slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 403)

    # ── Site artist list ──────────────────────────────────────────────────────

    def test_site_artist_list_includes_artist_who_showed_there(self):
        response = self.client.get(reverse('gallery:site_artist_list', kwargs={'site_slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.artist, response.context['artist_list'])

    def test_site_artist_list_excludes_artist_who_did_not_show_there(self):
        response = self.client.get(reverse('gallery:site_artist_list', kwargs={'site_slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.other_artist, response.context['artist_list'])

    def test_site_artist_list_paginates(self):
        """The scoped list shares the network list's paging.

        It used to be a separate view that rendered every artist at the venue in one
        response — fine for one gallery, not for a venue with a decade of shows.
        """
        response = self.client.get(
            reverse('gallery:site_artist_list', kwargs={'site_slug': self.published_site.slug}))
        self.assertIn('paginator', response.context)
        self.assertEqual(response.context['paginator'].per_page, 48)

    def test_site_artist_list_names_the_venue(self):
        response = self.client.get(
            reverse('gallery:site_artist_list', kwargs={'site_slug': self.published_site.slug}))
        self.assertContains(response, f'Artists at {self.published_site.name}')

    def test_network_artist_list_does_not_name_a_venue(self):
        response = self.client.get(reverse('gallery:artist_list'))
        self.assertEqual(response.context['site'], None)
        self.assertNotContains(response, 'Artists at ')

    def test_draft_site_artist_list_is_hidden_from_the_public(self):
        """Preserved from the view this replaced: a draft venue is not browsable."""
        response = self.client.get(
            reverse('gallery:site_artist_list', kwargs={'site_slug': self.draft_site.slug}))
        self.assertEqual(response.status_code, 404)

    # A real cache on purpose. settings.py forces DummyCache whenever 'test' is in argv,
    # so cached fragments cannot leak between tests — which also means no test can
    # exercise the grid cache unless it opts back in, as this one does.
    @override_settings(CACHES={'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'scoped-grid-cache-test'}})
    def test_scoped_and_network_grids_do_not_share_a_cache_entry(self):
        """The anonymous card fragment is cached; the venue has to be in its key.

        Without site.pk in the {% cache %} tag, a signed-out visitor to a venue's list is
        served whichever grid was rendered first — for the network list, that means seeing
        artists who have never shown at that venue.
        """
        from django.core.cache import cache
        cache.clear()
        network = self.client.get(reverse('gallery:artist_list'))
        scoped = self.client.get(
            reverse('gallery:site_artist_list', kwargs={'site_slug': self.published_site.slug}))
        self.assertContains(network, self.other_artist.name)      # network sees both
        self.assertNotContains(scoped, self.other_artist.name)    # the venue sees one

    # ── Site artwork list ─────────────────────────────────────────────────────

    def test_site_artwork_list_includes_artwork_shown_there(self):
        response = self.client.get(reverse('gallery:site_artwork_list', kwargs={'site_slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.artwork, response.context['artwork_list'])

    def test_site_artwork_list_excludes_artwork_not_shown_there(self):
        response = self.client.get(reverse('gallery:site_artwork_list', kwargs={'site_slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.other_artwork, response.context['artwork_list'])


class WallPlacementRotationGroupTests(TestCase):
    """Persistence of rotation and group through the layout save endpoint."""

    def setUp(self):
        import json
        self.json = json
        self.staff_user = User.objects.create_user(
            username='staff2@example.com', email='staff2@example.com', password='pw'
        )
        add_staff_role(self.staff_user)
        self.show = Show.objects.create(
            name='Layout Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        self.artwork = Artwork.objects.create(
            name='Sculpture', created_by=self.staff_user, end_year=2025,
            width_inches=20, height_inches=30, depth_inches=12,
        )
        self.show.artworks.add(self.artwork)
        self.client.force_login(self.staff_user)

    def _save(self, rotation, group):
        return self.client.post(
            reverse('gallery:room_layout_save', kwargs={'slug': self.show.slug}),
            data=self.json.dumps({'placements': [{
                'artwork_id': self.artwork.pk, 'wall': 'floor',
                'x_in': 5.0, 'y_in': 0.0, 'z_in': 3.0,
                'rotation': rotation, 'group': group,
            }]}),
            content_type='application/json',
        )

    def test_rotation_and_group_persist(self):
        from gallery.models import WallPlacement
        resp = self._save(rotation=90, group=7)
        self.assertEqual(resp.status_code, 200)
        wp = WallPlacement.objects.get(show=self.show, artwork=self.artwork)
        self.assertEqual(wp.rotation, 90)
        self.assertEqual(wp.group, 7)

    def test_rotation_clamped_and_group_null(self):
        from gallery.models import WallPlacement
        # rotation not a multiple of 90 collapses to 0; missing/blank group → None
        self._save(rotation=45, group=None)
        wp = WallPlacement.objects.get(show=self.show, artwork=self.artwork)
        self.assertEqual(wp.rotation, 0)
        self.assertIsNone(wp.group)

    def test_rotation_allows_full_quarter_turns(self):
        from gallery.models import WallPlacement
        for r in (0, 90, 180, 270):
            self._save(rotation=r, group=None)
            wp = WallPlacement.objects.get(show=self.show, artwork=self.artwork)
            self.assertEqual(wp.rotation, r)

    def test_viewer_serializes_depth(self):
        # depth_inches flows through to the placement JSON used by the viewers
        from gallery.views.room import _artwork_json
        data = _artwork_json(self.artwork)
        self.assertEqual(data['d_in'], 12.0)


class ArtistFormRequiredTests(TestCase):
    """The artist profile form enforces the fields shown under 'Required'."""

    def test_missing_required_fields_flagged(self):
        from gallery.forms import ArtistForm
        u = User.objects.create_user(
            username='af@example.com', email='af@example.com', password='pw'
        )
        form = ArtistForm(data={'first_name': 'A', 'last_name': 'B'}, user=u)
        self.assertFalse(form.is_valid())
        for f in ('email', 'zipcode', 'image'):
            self.assertIn(f, form.errors)

    def test_profile_needs_a_photo(self):
        """Required before submitting: chasing photos after acceptance costs the
        gallery far more than supplying one costs the artist."""
        from gallery.forms import ArtistForm
        u = User.objects.create_user(
            username='nophoto@example.com', email='nophoto@example.com', password='pw')
        data = {'first_name': 'A', 'last_name': 'B',
                'email': 'nophoto@example.com', 'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA'}
        self.assertFalse(ArtistForm(data=data, user=u).is_valid())
        form = ArtistForm(data=data, files={'image': _test_jpg('p.jpg')}, user=u)
        self.assertTrue(form.is_valid(), form.errors)

    def test_website_bare_domain_accepted_and_normalized(self):
        from gallery.forms import ArtistForm
        u = User.objects.create_user(
            username='afw@example.com', email='afw@example.com', password='pw')
        data = {'first_name': 'A', 'last_name': 'B', 'email': 'afw@example.com',
                'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA', 'website': 'howardhersh.com'}
        form = ArtistForm(data=data, user=u)
        form.is_valid()   # image missing, but website must NOT be an error
        self.assertNotIn('website', form.errors)
        self.assertEqual(form.cleaned_data.get('website'), 'https://howardhersh.com')

    def test_website_invalid_rejected(self):
        from gallery.forms import ArtistForm
        u = User.objects.create_user(
            username='afw2@example.com', email='afw2@example.com', password='pw')
        form = ArtistForm(data={'first_name': 'A', 'last_name': 'B', 'email': 'afw2@example.com',
                                'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA', 'website': 'not a url'}, user=u)
        form.is_valid()
        self.assertIn('website', form.errors)

    def test_form_groups_required_first(self):
        from gallery.forms import ArtistForm
        from crispy_forms.layout import Fieldset
        u = User.objects.create_user(
            username='af2@example.com', email='af2@example.com', password='pw'
        )
        legends = [f.legend for f in ArtistForm(user=u).helper.layout.fields
                   if isinstance(f, Fieldset)]
        self.assertEqual(legends[:2], ['Required', 'Optional'])

    def test_invalid_save_returns_to_form_with_data(self):
        """An invalid profile save must re-render the form (not redirect), keep the
        entered data, show the error, and save nothing."""
        u = User.objects.create_user(
            username='af3@example.com', email='af3@example.com', password='pw'
        )
        artist = Artist.objects.create(user=u, first_name='Pat', last_name='V', email='af3@example.com')
        self.client.force_login(u)
        url = reverse('gallery:artist_edit', kwargs={'pk': artist.pk})
        resp = self.client.post(url, data={
            'first_name': 'Pat', 'last_name': 'V', 'email': 'af3@example.com',
            'bio': 'My new bio text', 'zipcode': '',  # missing zip + photo
        })
        self.assertEqual(resp.status_code, 200)          # not a redirect
        body = resp.content.decode()
        self.assertIn('<form', body)                     # back on the form
        self.assertIn('My new bio text', body)           # entered data preserved
        artist.refresh_from_db()
        self.assertFalse(artist.bio)                     # nothing partially saved


class ArtworkFormFeedbackTests(TestCase):
    """Invalid artwork save returns to the form with data + a visible error."""

    def test_invalid_new_artwork_returns_to_form(self):
        u = User.objects.create_user(
            username='awf@example.com', email='awf@example.com', password='pw'
        )
        add_staff_role(u)
        self.client.force_login(u)
        before = Artwork.objects.count()
        resp = self.client.post(reverse('gallery:artwork_new'), data={
            'name': 'Untitled test piece', 'end_year': '2025', 'pricing_type': 'nfs',
            # deliberately missing required medium / width / height / image
        })
        self.assertEqual(resp.status_code, 200)                 # not a redirect
        body = resp.content.decode()
        self.assertIn('Untitled test piece', body)             # entered data preserved
        self.assertIn('Please correct the highlighted fields', body)
        self.assertEqual(Artwork.objects.count(), before)      # nothing saved

    def test_form_groups_required_first(self):
        from gallery.forms import ArtworkForm
        from crispy_forms.layout import Fieldset
        u = User.objects.create_user(
            username='awg@example.com', email='awg@example.com', password='pw'
        )
        add_staff_role(u)
        legends = [f.legend for f in ArtworkForm(user=u).helper.layout.fields
                   if isinstance(f, Fieldset)]
        self.assertEqual(legends, ['Required', 'Pricing', 'Additional details (optional)'])


class SubmissionsArtistFilterTests(TestCase):
    """The Submissions page can be filtered to one artist (invite-page links)."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='subf@example.com', email='subf@example.com', password='pw'
        )
        add_staff_role(self.staff)
        self.show = Show.objects.create(
            name='Filter Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        self.a1 = Artist.objects.create(first_name='Ann', last_name='One')
        self.a2 = Artist.objects.create(first_name='Bob', last_name='Two')
        self.w1 = Artwork.objects.create(name='Alpha Piece', end_year=2025)
        self.w1.artists.add(self.a1)
        self.w2 = Artwork.objects.create(name='Beta Piece', end_year=2025)
        self.w2.artists.add(self.a2)
        for w in (self.w1, self.w2):
            ArtworkSubmission.objects.create(show=self.show, artwork=w, submitted_by=self.staff)
        self.client.force_login(self.staff)

    def test_artist_param_filters_to_one_artist(self):
        url = reverse('gallery:show_submissions', kwargs={'slug': self.show.slug})
        body = self.client.get(url, {'artist': self.a1.pk}).content.decode()
        self.assertIn('Alpha Piece', body)
        self.assertNotIn('Beta Piece', body)

    def test_no_param_shows_all(self):
        url = reverse('gallery:show_submissions', kwargs={'slug': self.show.slug})
        body = self.client.get(url).content.decode()
        self.assertIn('Alpha Piece', body)
        self.assertIn('Beta Piece', body)


class SanitizeFilterTests(TestCase):
    """The |sanitize filter must strip XSS but keep safe formatting."""

    def _s(self, v):
        from gallery.templatetags.site_tags import sanitize
        return sanitize(v)

    def test_strips_script_and_handlers(self):
        out = self._s('<b>hi</b><script>alert(1)</script><img src=x onerror=alert(1)>')
        self.assertIn('<b>hi</b>', out)
        self.assertNotIn('<script', out)
        self.assertNotIn('onerror', out)

    def test_strips_dangerous_url_scheme(self):
        out = self._s('<a href="javascript:alert(1)">x</a>')
        self.assertNotIn('javascript:', out)

    def test_keeps_safe_link(self):
        out = self._s('<a href="https://ok.com">ok</a>')
        self.assertIn('href="https://ok.com"', out)

    def test_empty(self):
        self.assertEqual(self._s(''), '')
        self.assertEqual(self._s(None), '')


class SupportSaveTests(TestCase):
    """Supports (pedestals/shelves) persist and link to the pieces on them."""

    def setUp(self):
        import json
        self.json = json
        self.staff = User.objects.create_user(
            username='sup-staff@example.com', email='sup-staff@example.com', password='pw')
        add_staff_role(self.staff)
        self.show = Show.objects.create(
            name='Support Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7))
        self.artwork = Artwork.objects.create(
            name='Bust', end_year=2025, width_inches=10, height_inches=14, depth_inches=8)
        self.show.artworks.add(self.artwork)
        self.client.force_login(self.staff)

    def test_support_and_link_persist(self):
        from gallery.models import Support, WallPlacement
        payload = {
            'supports': [{'key': 's1', 'wall': 'N', 'x_in': 0, 'y_in': 48,
                          'z_in': 0, 'w_in': 36, 'h_in': 2, 'd_in': 8, 'rotation': 90, 'label': 'Shelf A'}],
            'placements': [{'artwork_id': self.artwork.pk, 'wall': 'N', 'x_in': 0, 'y_in': 50,
                            'z_in': 0, 'rotation': 0, 'group': None, 'support': 's1'}],
        }
        r = self.client.post(
            reverse('gallery:room_layout_save', kwargs={'slug': self.show.slug}),
            data=self.json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        s = Support.objects.get(show=self.show)
        self.assertEqual(s.w_in, 36.0)
        self.assertEqual(s.rotation, 90)   # rotation persists
        wp = WallPlacement.objects.get(show=self.show)
        self.assertEqual(wp.support_id, s.pk)

    def test_support_texture_persists(self):
        from gallery.models import Support
        payload = {
            'supports': [{'key': 's1', 'wall': 'floor', 'x_in': 0, 'y_in': 0, 'z_in': 0,
                          'w_in': 16, 'h_in': 40, 'd_in': 16, 'rotation': 0,
                          'label': 'Plinth', 'texture': '/media/support_textures/marble.jpg'}],
            'placements': [],
        }
        r = self.client.post(
            reverse('gallery:room_layout_save', kwargs={'slug': self.show.slug}),
            data=self.json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        s = Support.objects.get(show=self.show)
        self.assertEqual(s.texture_url, '/media/support_textures/marble.jpg')

    def test_save_support_to_catalog(self):
        from gallery.models import Site, SiteSupport
        site = Site.objects.create(name='Cat Site', status=Site.STATUS_PUBLISHED)
        self.show.sites.add(site)
        r = self.client.post(
            reverse('gallery:save_support_to_catalog', kwargs={'slug': self.show.slug}),
            data=self.json.dumps({'label': 'Shelf X', 'w_in': 40, 'h_in': 2, 'd_in': 10}),
            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(SiteSupport.objects.filter(label='Shelf X', room_config__site=site).exists())

    def test_save_support_to_catalog_ignores_bad_texture(self):
        # A texture URL that doesn't resolve to a stored file must not break the
        # save — the named support is still created (texture is optional).
        from gallery.models import Site, SiteSupport
        site = Site.objects.create(name='Cat Site 2', status=Site.STATUS_PUBLISHED)
        self.show.sites.add(site)
        r = self.client.post(
            reverse('gallery:save_support_to_catalog', kwargs={'slug': self.show.slug}),
            data=self.json.dumps({'label': 'Plinth Y', 'w_in': 16, 'h_in': 40, 'd_in': 16,
                                  'texture': 'https://example.com/support_textures/missing.png'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        cat = SiteSupport.objects.get(label='Plinth Y', room_config__site=site)
        self.assertFalse(bool(cat.texture))


class ChecklistPdfTests(TestCase):
    """Exhibition checklist PDF (cover, works+images, artist & curator bios)."""

    def _jpg(self, name):
        import io
        from PIL import Image as P
        b = io.BytesIO(); P.new('RGB', (300, 200), (150, 120, 90)).save(b, 'JPEG')
        return SimpleUploadedFile(name, b.getvalue(), content_type='image/jpeg')

    def setUp(self):
        from gallery.models import Artist, Site, Event
        self.staff = User.objects.create_user(
            username='cl@example.com', email='cl@example.com', password='pw')
        add_staff_role(self.staff)
        self.site = Site.objects.create(
            name='Personal Space', street='1505 Tennessee Street', city='Vallejo',
            state='CA', postal_code='94590', website='www.personalspace.space',
            instagram='@personal_space', image=self._jpg('logo.jpg'))
        self.curator = Artist.objects.create(
            name='Reniel del Rosario', first_name='Reniel', last_name='del Rosario',
            email='cur@e.com', bio='A curator working across ceramics.', instagram='@reniel',
            image=self._jpg('cur.jpg'))
        self.show = Show.objects.create(
            name='Giant Steps', description='A statement.\n\nAnother paragraph.',
            start=datetime.date(2026, 5, 31), end=datetime.date(2026, 7, 19))
        self.show.curators.add(self.curator)
        self.show.sites.add(self.site)
        Event.objects.create(name='Opening', show=self.show, date=datetime.date(2026, 5, 31),
                             start=datetime.time(14, 0), end=datetime.time(17, 0))
        artist = Artist.objects.create(
            name='Paola de la Calle', first_name='Paola', last_name='Calle', email='a@e.com',
            bio='An artist working in ceramics and mixed media.', instagram='@paola',
            image=self._jpg('artist.jpg'))
        aw = Artwork.objects.create(
            name='Herencia', end_year=2024, medium='Found object, glazed stoneware',
            width_inches=19, height_inches=9, depth_inches=1,
            pricing_type='for_sale', price=550, image=self._jpg('work.jpg'))
        aw.artists.add(artist)
        self.show.artworks.add(aw)
        self.url = reverse('gallery:show_checklist_pdf', kwargs={'slug': self.show.slug})

    def test_staff_downloads_checklist(self):
        self.client.force_login(self.staff)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF-'))
        self.assertIn('attachment', r['Content-Disposition'])

    def test_non_manager_denied(self):
        other = User.objects.create_user(username='no@e.com', email='no@e.com', password='pw')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_bio_entry_shows_name_and_image_without_bio(self):
        from reportlab.platypus import ImageAndFlowables, Paragraph
        from gallery.models import Artist
        from gallery.views.checklist import _bio_entry, _styles
        # No bio/statement, but has an image → still an image+name block.
        a = Artist.objects.create(name='No Bio', first_name='No', last_name='Bio',
                                  email='nb@e.com', image=self._jpg('nb.jpg'))
        story = []
        _bio_entry(a, _styles(), story)
        self.assertTrue(any(isinstance(f, ImageAndFlowables) for f in story))
        # No image and no bio → still a name paragraph (never dropped entirely).
        b = Artist.objects.create(name='Bare', first_name='Bare', last_name='X', email='bx@e.com')
        story2 = []
        _bio_entry(b, _styles(), story2)
        self.assertTrue(any(isinstance(f, Paragraph) for f in story2))

    def test_downscale_applies_exif_orientation(self):
        import io
        from PIL import Image as P
        from gallery.views.checklist import _downscale
        im = P.new('RGB', (100, 40), (200, 50, 50))
        b = io.BytesIO(); exif = im.getexif(); exif[274] = 6   # Orientation = rotate 90
        im.save(b, 'JPEG', exif=exif.tobytes()); data = b.getvalue()

        class F:
            def open(self, mode='rb'): self._b = io.BytesIO(data)
            def read(self): return self._b.read()
            def close(self): pass
            def __bool__(self): return True

        got = _downscale(F(), 600)
        self.assertIsNotNone(got)
        _, w, h = got
        self.assertEqual((w, h), (40, 100))   # landscape source rotated to portrait

    def test_html_checklist_is_public_for_a_published_show(self):
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])
        url = reverse('gallery:show_checklist', kwargs={'slug': self.show.slug})
        r = self.client.get(url)                      # anonymous
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn(self.show.name, body)
        self.assertIn('float: left', body)            # thumbnails flow, per the PDF
        # The curator-only PDF is not advertised to the public.
        self.assertNotIn('checklist.pdf', body)

    def test_html_checklist_hidden_until_published(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        url = reverse('gallery:show_checklist', kwargs={'slug': self.show.slug})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.staff)           # curator may preview early
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_html_checklist_carries_no_private_contact_details(self):
        """It is public, so it must hold no more than the PDF does."""
        from gallery.models import Artist
        artist = Artist.objects.create(
            name='Pia Private', first_name='Pia', last_name='Private',
            email='pia-private@example.com', phone='555-0199', venmo='@pia-venmo',
            bio='A bio.')
        art = Artwork.objects.create(name='Piece', end_year=2025)
        art.artists.add(artist)
        self.show.artworks.add(art)
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save(update_fields=['status'])

        body = self.client.get(
            reverse('gallery:show_checklist', kwargs={'slug': self.show.slug})).content.decode()
        self.assertIn('Pia Private', body)             # credited
        self.assertIn('A bio.', body)
        for secret in ('pia-private@example.com', '555-0199', '@pia-venmo'):
            self.assertNotIn(secret, body)

    def test_pdf_and_html_checklists_agree_on_content(self):
        """Both read from _checklist_data, so neither can quietly drift."""
        from gallery.views.checklist import _checklist_data
        site, works, artists, curators = _checklist_data(self.show)
        self.assertEqual([str(w) for w in works],
                         [str(w) for w in _checklist_data(self.show, user=self.staff)[1]])
        self.assertTrue(works and artists)

    def _counting_field(self, name, opens, fail=False):
        """A stand-in image field that records every storage open."""
        import io
        from PIL import Image as P
        b = io.BytesIO(); P.new('RGB', (40, 30), (10, 20, 30)).save(b, 'JPEG')
        data = b.getvalue()

        class F:
            def __init__(self): self.name = name
            def __bool__(self): return True
            def open(self, mode='rb'):
                opens.append(name)
                if fail:
                    raise IOError('missing')
                self._b = io.BytesIO(data)
            def read(self): return self._b.read()
            def close(self): pass
        return F()

    def test_prefetch_warms_cache_and_skips_full_size_originals(self):
        """Images are fetched once, concurrently, before the PDF is laid out. Only the
        thumbnail candidate is prefetched — pulling the full-resolution fallbacks up
        front would download megabytes that are almost never needed."""
        from gallery.views.checklist import _downscale, _prefetch
        opens = []
        lists = [[self._counting_field(f'thumb-{i}.jpg', opens),
                  self._counting_field(f'orig-{i}.jpg', opens)] for i in range(5)]

        cache = {}
        _prefetch(lists, cache)
        self.assertEqual(sorted(opens), [f'thumb-{i}.jpg' for i in range(5)])
        self.assertFalse([k for k in cache if k.startswith('orig-')])

        opens.clear()
        for l in lists:
            self.assertIsNotNone(_downscale(l, 600, cache))
        self.assertEqual(opens, [])   # laid out entirely from the prefetched bytes

    def test_missing_thumbnail_falls_back_to_original_and_warns(self):
        from gallery.views.checklist import _downscale, _prefetch
        opens = []
        fields = [self._counting_field('thumb.jpg', opens, fail=True),
                  self._counting_field('orig.jpg', opens)]
        cache = {}
        _prefetch([fields], cache)
        with self.assertLogs('gallery.views.checklist', level='WARNING') as log:
            self.assertIsNotNone(_downscale(fields, 600, cache))
        self.assertIn('thumbnail missing', ''.join(log.output))
        self.assertIn('orig.jpg', opens)   # fallback fetched lazily, not in the prefetch

    def test_logo_reader_uses_site_icon(self):
        from gallery.models import Site
        from gallery.views.checklist import _logo_reader
        self.assertIsNone(_logo_reader(Site.objects.create(name='No Logo')))
        self.assertIsNotNone(_logo_reader(Site.objects.create(name='Has Logo', icon=self._jpg('icon.jpg'))))

    def test_plain_strips_html(self):
        from gallery.views.checklist import _plain
        self.assertEqual(_plain('<p>Hi <b>x</b> &amp; y</p>'), 'Hi x & y')
        self.assertEqual(_plain('<p>One</p><p>Two</p>'), 'One\n\nTwo')

    def test_bio_entry_labels_and_instagram(self):
        from reportlab.platypus import Paragraph
        from gallery.models import Artist
        from gallery.views.checklist import _bio_entry, _styles
        a = Artist.objects.create(name='Zed', first_name='Z', last_name='D', email='z@e.com',
                                  bio='<p>bio text</p>', statement='stmt text', instagram='@zed')
        story = []
        _bio_entry(a, _styles(), story)
        texts = [f.text for f in story if isinstance(f, Paragraph)]
        self.assertTrue(any('@zed' in t and t.startswith('<b>') for t in texts))          # IG by name
        self.assertTrue(any('bio text' in t for t in texts))                              # bio shown
        self.assertFalse(any(t.startswith('<b>Bio:</b>') for t in texts))                 # no "Bio:" label
        self.assertTrue(any(t.startswith('<b>Statement:</b>') and 'stmt text' in t for t in texts))

    def test_cover_uses_date_range_not_name_twice(self):
        from reportlab.platypus import Paragraph
        from reportlab.lib.units import inch
        from gallery.views.checklist import _cover, _styles
        story = _cover(self.show, self.site, list(self.show.artworks.all()), _styles(), 6.5 * inch)
        texts = [f.text for f in story if isinstance(f, Paragraph)]
        self.assertIn(self.show.date_range, texts)                                   # run dates present
        self.assertEqual(sum(1 for t in texts if t == self.show.name), 1)           # name only once

    def test_minimal_show_renders(self):
        # No site, no images, no bios, no events → must still produce a PDF.
        bare = Show.objects.create(name='Bare Show', start=datetime.date.today(),
                                   end=datetime.date.today() + datetime.timedelta(days=3))
        aw = Artwork.objects.create(name='Plain', end_year=2025, medium='oil',
                                    width_inches=10, height_inches=10)
        bare.artworks.add(aw)
        self.client.force_login(self.staff)
        r = self.client.get(reverse('gallery:show_checklist_pdf', kwargs={'slug': bare.slug}))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b'%PDF-'))


class LayoutSnapshotTests(TestCase):
    """Layout snapshots: auto safety net before saves/restores, named restore
    points, and the export/import management commands."""

    def setUp(self):
        import json
        self.json = json
        self.staff = User.objects.create_user(
            username='snap-staff@example.com', email='snap-staff@example.com', password='pw')
        add_staff_role(self.staff)
        self.show = Show.objects.create(
            name='Snap Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7))
        self.artwork = Artwork.objects.create(name='Piece', end_year=2025,
                                              width_inches=10, height_inches=14)
        self.show.artworks.add(self.artwork)
        self.client.force_login(self.staff)
        self.save_url = reverse('gallery:room_layout_save', kwargs={'slug': self.show.slug})

    def _save(self, x):
        payload = {'supports': [], 'placements': [
            {'artwork_id': self.artwork.pk, 'wall': 'N', 'x_in': x, 'y_in': 50,
             'z_in': 0, 'rotation': 0, 'group': None, 'support': None}]}
        return self.client.post(self.save_url, data=self.json.dumps(payload),
                                content_type='application/json')

    def test_layout_save_does_not_change_room_dimensions(self):
        from gallery.models import RoomConfig, Site
        site = Site.objects.create(name='Venue', status=Site.STATUS_PUBLISHED)
        cfg = RoomConfig.objects.create(site=site, width_in=384, depth_in=576, height_in=120)
        self.show.sites.add(site)
        # A layout save carrying stale room dims must NOT overwrite the site config.
        payload = {'room': {'width_in': 240, 'depth_in': 240, 'height_in': 96},
                   'supports': [], 'placements': []}
        r = self.client.post(self.save_url, data=self.json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        cfg.refresh_from_db()
        self.assertEqual((cfg.width_in, cfg.depth_in, cfg.height_in), (384.0, 576.0, 120.0))

    def test_layout_save_snaps_stale_wall_coordinates(self):
        """A client that loaded before a room resize POSTs the OLD wall plane. The
        save must re-derive it, or the piece lands outside the room and the 3D viewer
        hides it behind the opaque wall. Along-wall position and height are kept, and
        floor pieces (no perpendicular axis) are left alone."""
        from gallery.models import RoomConfig, Site, Support, WallPlacement
        site = Site.objects.create(name='Venue', status=Site.STATUS_PUBLISHED)
        RoomConfig.objects.create(site=site, width_in=348, depth_in=565, height_in=120)
        self.show.sites.add(site)
        floor_art = Artwork.objects.create(name='Pedestal piece', end_year=2025)
        self.show.artworks.add(floor_art)
        # Every coordinate below is from the previous 384×576 room.
        payload = {'supports': [{'key': 'k1', 'wall': 'W', 'label': 'Shelf',
                                 'x_in': -192, 'y_in': 34.7, 'z_in': 55.68,
                                 'w_in': 16, 'h_in': 4, 'd_in': 10}],
                   'placements': [
                       {'artwork_id': self.artwork.pk, 'wall': 'E',
                        'x_in': 192, 'y_in': 60, 'z_in': -100.28},
                       {'artwork_id': floor_art.pk, 'wall': 'floor',
                        'x_in': 100.68, 'y_in': 34.7, 'z_in': 168.58}]}
        r = self.client.post(self.save_url, data=self.json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)

        east = WallPlacement.objects.get(show=self.show, artwork=self.artwork)
        self.assertEqual(east.x_in, 174.0)        # snapped to the current east wall
        self.assertEqual(east.z_in, -100.28)      # along-wall position preserved
        self.assertEqual(east.y_in, 60.0)         # height preserved

        floor = WallPlacement.objects.get(show=self.show, artwork=floor_art)
        self.assertEqual((floor.x_in, floor.y_in, floor.z_in), (100.68, 34.7, 168.58))

        shelf = Support.objects.get(show=self.show)
        self.assertEqual(shelf.x_in, -174.0)      # wall shelves snap the same way
        self.assertEqual(shelf.z_in, 55.68)

    def test_save_auto_snapshots_prior_state(self):
        from gallery.models import ShowLayoutSnapshot, WallPlacement
        self._save(5)      # first save: prior state empty → no snapshot
        self.assertEqual(ShowLayoutSnapshot.objects.filter(show=self.show).count(), 0)
        self._save(99)     # second save: snapshots the prior (x=5) state
        autos = ShowLayoutSnapshot.objects.filter(show=self.show, kind=ShowLayoutSnapshot.AUTO)
        self.assertEqual(autos.count(), 1)
        self.assertEqual(autos.first().payload['placements'][0]['x_in'], 5)
        # Current DB reflects the latest save.
        self.assertEqual(WallPlacement.objects.get(show=self.show).x_in, 99)

    def test_manual_snapshot_and_restore(self):
        from gallery.models import ShowLayoutSnapshot, WallPlacement
        self._save(5)
        r = self.client.post(reverse('gallery:layout_snapshots', kwargs={'slug': self.show.slug}),
                             data=self.json.dumps({'name': 'Good layout'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        snap = ShowLayoutSnapshot.objects.get(show=self.show, kind=ShowLayoutSnapshot.MANUAL)
        self.assertEqual(snap.name, 'Good layout')
        self._save(99)     # clobber
        self.assertEqual(WallPlacement.objects.get(show=self.show).x_in, 99)
        # Restore the named snapshot → x back to 5
        r = self.client.post(reverse('gallery:restore_layout_snapshot',
                                     kwargs={'slug': self.show.slug, 'pk': snap.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WallPlacement.objects.get(show=self.show).x_in, 5)

    def test_delete_snapshot(self):
        from gallery.models import ShowLayoutSnapshot
        self._save(5)
        r = self.client.post(reverse('gallery:layout_snapshots', kwargs={'slug': self.show.slug}),
                             data=self.json.dumps({'name': 'Doomed'}), content_type='application/json')
        snap_id = r.json()['snapshot']['id']
        r = self.client.post(reverse('gallery:delete_layout_snapshot',
                                     kwargs={'slug': self.show.slug, 'pk': snap_id}))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ShowLayoutSnapshot.objects.filter(pk=snap_id).exists())

    def test_delete_snapshot_requires_manage(self):
        from gallery.models import ShowLayoutSnapshot
        self._save(5)
        r = self.client.post(reverse('gallery:layout_snapshots', kwargs={'slug': self.show.slug}),
                             data=self.json.dumps({'name': 'Keep'}), content_type='application/json')
        snap_id = r.json()['snapshot']['id']
        other = User.objects.create_user(username='z@e.com', email='z@e.com', password='pw')
        self.client.force_login(other)
        r = self.client.post(reverse('gallery:delete_layout_snapshot',
                                     kwargs={'slug': self.show.slug, 'pk': snap_id}))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(ShowLayoutSnapshot.objects.filter(pk=snap_id).exists())

    def test_restore_requires_manage(self):
        from gallery.models import ShowLayoutSnapshot
        self._save(5)
        self._save(9)
        snap = ShowLayoutSnapshot.objects.filter(show=self.show).first()
        other = User.objects.create_user(username='x@e.com', email='x@e.com', password='pw')
        self.client.force_login(other)
        r = self.client.post(reverse('gallery:restore_layout_snapshot',
                                     kwargs={'slug': self.show.slug, 'pk': snap.pk}))
        self.assertEqual(r.status_code, 404)

    def test_stale_save_is_rejected(self):
        from gallery.models import WallPlacement
        # Load a revision, save once (rev advances), then try to save again with the
        # OLD rev — simulating a stale tab. It must be refused, not clobber.
        rev0 = self.client.get(reverse('gallery:room_layout', kwargs={'slug': self.show.slug}))
        self._save(5)                      # advances the server revision
        payload = {'rev': '0:0:0:0', 'supports': [], 'placements': [
            {'artwork_id': self.artwork.pk, 'wall': 'N', 'x_in': 77, 'y_in': 50,
             'z_in': 0, 'rotation': 0, 'group': None, 'support': None}]}
        r = self.client.post(self.save_url, data=self.json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json().get('stale'))
        # The stale save did NOT take effect.
        self.assertEqual(WallPlacement.objects.get(show=self.show).x_in, 5)

    def test_current_rev_save_succeeds_and_returns_new_rev(self):
        r1 = self.client.post(self.save_url, data=self.json.dumps(
            {'rev': '', 'supports': [], 'placements': []}), content_type='application/json')
        rev = r1.json()['rev']
        payload = {'rev': rev, 'supports': [], 'placements': [
            {'artwork_id': self.artwork.pk, 'wall': 'N', 'x_in': 8, 'y_in': 50,
             'z_in': 0, 'rotation': 0, 'group': None, 'support': None}]}
        r2 = self.client.post(self.save_url, data=self.json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()['ok'])
        self.assertNotEqual(r2.json()['rev'], rev)   # revision advanced

    def test_force_overwrites_stale(self):
        from gallery.models import WallPlacement
        self._save(5)
        payload = {'rev': '0:0:0:0', 'force': True, 'supports': [], 'placements': [
            {'artwork_id': self.artwork.pk, 'wall': 'N', 'x_in': 77, 'y_in': 50,
             'z_in': 0, 'rotation': 0, 'group': None, 'support': None}]}
        r = self.client.post(self.save_url, data=self.json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WallPlacement.objects.get(show=self.show).x_in, 77)

    def test_export_import_roundtrip(self):
        import tempfile, os
        from django.core.management import call_command
        from gallery.models import WallPlacement
        self._save(42)
        fd, path = tempfile.mkstemp(suffix='.json'); os.close(fd)
        try:
            call_command('export_layout', self.show.slug, '--out', path)
            self._save(7)   # change it
            self.assertEqual(WallPlacement.objects.get(show=self.show).x_in, 7)
            call_command('import_layout', self.show.slug, path)
            self.assertEqual(WallPlacement.objects.get(show=self.show).x_in, 42)
        finally:
            os.remove(path)


class PlacardSheetPdfTests(TestCase):
    """Printable Avery 5376 placard sheet."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='pp@example.com', email='pp@example.com', password='pw')
        add_staff_role(self.staff)
        self.show = Show.objects.create(
            name='Placard Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=5))
        from gallery.models import Artist, ShowArtworkNumber
        artist = Artist.objects.create(name='Ada L', first_name='Ada', last_name='L', email='a@e.com')
        for i in range(1, 13):
            aw = Artwork.objects.create(
                name='A Rather Long Artwork Title %d' % i, end_year=2025,
                medium='oil on canvas', width_inches=24, height_inches=36,
                pricing_type='for_sale', price=1000 + i)
            aw.artists.add(artist)
            self.show.artworks.add(aw)
            ShowArtworkNumber.objects.create(show=self.show, artwork=aw, number=i)
        self.url = reverse('gallery:placard_sheet_pdf', kwargs={'slug': self.show.slug})

    def test_staff_downloads_pdf(self):
        self.client.force_login(self.staff)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF-'))
        self.assertIn('attachment', r['Content-Disposition'])

    def test_outlines_variant(self):
        self.client.force_login(self.staff)
        r = self.client.get(self.url + '?outlines=1')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b'%PDF-'))

    def test_collaboration_credits_every_artist_alphabetically(self):
        """A piece with several artists names all of them, comma-separated and in the
        same order, wherever it is printed or displayed. Artist.Meta.ordering is
        ['-created_at'], so a plain artists.all() would reverse them by sign-up date."""
        from gallery.models import Artist
        from gallery.views.checklist import _styles, _work_entry
        from gallery.views.placards import _card_fields, _layout_card, CARD_W, CARD_H, PAD
        from gallery.views.room import _artwork_json

        aw = Artwork.objects.create(name='Collaboration', end_year=2025,
                                    medium='mixed media', width_inches=24, height_inches=36)
        for first, last in [('Ana', 'Ruiz'), ('Cleo', 'Nakamura'), ('Bo', 'Chen')]:
            aw.artists.add(Artist.objects.create(
                name='%s %s' % (first, last), first_name=first, last_name=last,
                email='%s@example.com' % first.lower()))
        expected = 'Ana Ruiz, Bo Chen, Cleo Nakamura'

        self.assertEqual(aw.credit_line, expected)
        # ...and not merely whatever the default related ordering produced.
        self.assertNotEqual(', '.join(str(a) for a in aw.artists.all()), expected)

        self.assertIn(expected, [t for (t, *_rest) in _card_fields(aw)])
        card = ' '.join(t for (t, _f, _s) in _layout_card(aw, CARD_W - 2 * PAD, CARD_H - 2 * PAD))
        for name in ('Ana Ruiz', 'Bo Chen', 'Cleo Nakamura'):
            self.assertIn(name, card)
        self.assertNotIn('…', card)                       # nobody trimmed off the card
        self.assertEqual(_artwork_json(aw)['artists'], expected)

        entry = _work_entry(aw, _styles(), 6.5 * 72)
        text = ''
        for row in entry._content:
            if hasattr(row, 'text'):
                text = row.text
            for cells in getattr(row, '_cellvalues', None) or []:
                for cell in cells:
                    if hasattr(cell, 'text'):
                        text = cell.text
        self.assertIn(expected, text)

    def test_magtag_placard_uses_the_same_names_as_the_printed_one(self):
        """The e-ink placard read the raw `name` column while paper used full_name,
        so the two could disagree for the same piece."""
        from gallery.models import Artist, ShowArtworkNumber
        from gallery.views.placards import _get_placard_data
        aw = Artwork.objects.create(name='Duo Piece', end_year=2025)
        aw.artists.add(Artist.objects.create(
            name='legacy-handle', first_name='Bo', last_name='Chen', email='b@e.com'))
        aw.artists.add(Artist.objects.create(
            name='other-handle', first_name='Ana', last_name='Ruiz', email='a@e.com'))
        self.show.artworks.add(aw)
        ShowArtworkNumber.objects.create(show=self.show, artwork=aw, number=99)

        data = _get_placard_data(self.client.request().wsgi_request, self.show, 99)
        self.assertEqual(data['artwork']['artists'], ['Ana Ruiz', 'Bo Chen'])

    def _pages(self, qs=''):
        r = self.client.get(self.url + qs)
        self.assertEqual(r.status_code, 200)
        return len(re.findall(rb'/Type\s*/Page[^s]', r.content))

    def test_selected_ids_limit_the_sheet(self):
        """A subset prints only those cards, packed from the first slot — so a few
        reprints do not mean running the whole show again."""
        self.client.force_login(self.staff)
        ids = list(self.show.artworks.values_list('pk', flat=True))
        self.assertEqual(self._pages(), 2)                       # 12 works, 10 per sheet
        self.assertEqual(self._pages('?ids=%d&ids=%d&ids=%d' % tuple(ids[:3])), 1)
        self.assertEqual(self._pages('?ids=' + ','.join(str(i) for i in ids[:11])), 2)

    def test_selection_filename_distinguishes_a_partial_sheet(self):
        self.client.force_login(self.staff)
        pk = self.show.artworks.first().pk
        self.assertIn('placards-%s.pdf' % self.show.slug,
                      self.client.get(self.url)['Content-Disposition'])
        self.assertIn('selection-1.pdf',
                      self.client.get(self.url + '?ids=%d' % pk)['Content-Disposition'])

    def test_ids_cannot_pull_in_another_shows_artwork(self):
        other = Show.objects.create(name='Other Show', start=datetime.date.today(),
                                    end=datetime.date.today())
        alien = Artwork.objects.create(name='Alien', end_year=2025)
        other.artworks.add(alien)
        self.client.force_login(self.staff)
        r = self.client.get(self.url + '?ids=%d' % alien.pk)
        self.assertEqual(r.status_code, 400)
        # Mixed selection keeps only the artwork that belongs to this show.
        mine = self.show.artworks.first().pk
        r = self.client.get(self.url + '?ids=%d,%d' % (mine, alien.pk))
        self.assertEqual(r.status_code, 200)
        self.assertIn('selection-1.pdf', r['Content-Disposition'])

    def test_unparseable_ids_do_not_silently_print_everything(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url + '?ids=abc,,').status_code, 400)

    def test_picker_form_is_curator_only(self):
        """The page stays public (PublicUrlTests pins that), but only a curator gets
        the selection form — it submits to an endpoint they alone can use."""
        url = reverse('gallery:show_placards_detail', kwargs={'slug': self.show.slug})
        sheet = reverse('gallery:placard_sheet_pdf', kwargs={'slug': self.show.slug})

        anon = self.client.get(url)
        self.assertEqual(anon.status_code, 200)
        self.assertNotIn('name="ids"', anon.content.decode())
        self.assertNotIn(sheet, anon.content.decode())

        self.client.force_login(self.staff)
        body = self.client.get(url).content.decode()
        self.assertEqual(body.count('name="ids"'), 12)            # one box per artwork
        self.assertIn(sheet, body)

    def test_card_fields_content(self):
        from gallery.models import Artist
        from gallery.views.placards import _card_fields
        ar = Artist.objects.create(name='Ada', first_name='Ada', last_name='L', email='a@e.com')
        aw = Artwork.objects.create(name='Study', start_year=2019, end_year=2021,
                                    medium='oil on canvas', width_inches=24, height_inches=36,
                                    pricing_type='for_sale', price=4321)
        aw.artists.add(ar)
        texts = [t for (t, f, s, m) in _card_fields(aw)]
        self.assertEqual(texts, ['Study', '2019–2021', 'Ada L', 'oil on canvas', '24 × 36 in'])
        self.assertNotIn('4321', ''.join(texts))          # no price
        self.assertFalse(any(t.startswith('#') for t in texts))   # no number

    def test_long_medium_wraps_and_fits(self):
        from gallery.models import Artist
        from gallery.views.placards import _card_fields, _layout_card, LEADING, PAD, CARD_H
        ar = Artist.objects.create(name='Ada', first_name='Ada', last_name='L', email='a@e.com')
        aw = Artwork.objects.create(
            name='An Unusually Long Artwork Title That Will Need To Wrap Or Shrink',
            end_year=2025, width_inches=24, height_inches=36,
            medium='archival pigment print on cotton rag with hand-applied gold leaf and resin varnish')
        aw.artists.add(ar)
        avail_w, avail_h = 160.0, CARD_H - 2 * PAD
        lines = _layout_card(aw, avail_w, avail_h)
        # everything fits vertically...
        self.assertLessEqual(sum(s * LEADING for _, _, s in lines), avail_h + 0.5)
        # ...and every line fits the width
        from reportlab.pdfbase.pdfmetrics import stringWidth
        for text, font, size in lines:
            self.assertLessEqual(stringWidth(text, font, size), avail_w + 0.5)

    def test_multiline_field_uses_smaller_font(self):
        from gallery.views.placards import _fit_field, _FONT
        short_lines, short_size = _fit_field('oil', _FONT, 9.0, 160.0, 2)
        long_lines, long_size = _fit_field(
            'archival pigment print on cotton rag with hand-applied gold leaf and resin varnish',
            _FONT, 9.0, 160.0, 2)
        self.assertEqual(len(short_lines), 1)
        self.assertEqual(short_size, 9.0)              # single line keeps full size
        self.assertGreater(len(long_lines), 1)
        self.assertLess(long_size, 9.0)                # wrapped field shrinks

    def test_unicode_font_registered(self):
        from gallery.views.placards import _FONT, _IS_TTF
        self.assertTrue(_IS_TTF)                       # a Unicode TrueType font, not base-14
        self.assertIn(_FONT, ('DejaVuSans', 'Vera'))   # DejaVu preferred, Vera fallback

    def test_unicode_medium_renders(self):
        from gallery.models import Artist
        ar = Artist.objects.create(name='Zoë', first_name='Zoë', last_name='Müller', email='z@e.com')
        aw = Artwork.objects.create(
            name='Café — Étude № 3 (Привет)', end_year=2025, width_inches=24, height_inches=36,
            medium='pigment print on cotton rag — résine & séraphin varnish, édition of 5')
        aw.artists.add(ar)
        self.show.artworks.add(aw)
        self.client.force_login(self.staff)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)           # no crash on non-Latin-1 characters
        self.assertTrue(r.content.startswith(b'%PDF-'))

    def test_qr_toggle(self):
        self.client.force_login(self.staff)
        with_qr = self.client.get(self.url).content
        no_qr = self.client.get(self.url + '?qr=0').content
        self.assertTrue(with_qr.startswith(b'%PDF-') and no_qr.startswith(b'%PDF-'))
        self.assertGreater(len(with_qr), len(no_qr))   # QR adds content

    def test_non_manager_denied(self):
        other = User.objects.create_user(username='nn@e.com', email='nn@e.com', password='pw')
        self.client.force_login(other)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 404)


class RoomCameraSaveTests(TestCase):
    """Curator/admin-set default start viewpoint for the 3D walkthrough."""

    def setUp(self):
        import json
        from gallery.models import RoomConfig, Site
        self.json = json
        self.staff = User.objects.create_user(
            username='cam-staff@example.com', email='cam-staff@example.com', password='pw')
        add_staff_role(self.staff)
        self.viewer = User.objects.create_user(
            username='cam-viewer@example.com', email='cam-viewer@example.com', password='pw')
        self.show = Show.objects.create(
            name='Cam Show', status=Show.STATUS_PUBLISHED, start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7))
        self.site = Site.objects.create(name='Cam Venue', status=Site.STATUS_PUBLISHED)
        self.cfg = RoomConfig.objects.create(site=self.site, width_in=384, depth_in=576, height_in=120)
        self.show.sites.add(self.site)
        self.url = reverse('gallery:room_camera_save', kwargs={'slug': self.show.slug})
        self.pose = {'p': [0, 1.5, -7], 'q': [0, 1, 0, 0], 'yaw': 3.14, 'pitch': 0}

    def _post(self, body):
        return self.client.post(self.url, data=self.json.dumps(body),
                                content_type='application/json')

    def test_curator_can_save_start_view(self):
        self.client.force_login(self.staff)
        r = self._post(self.pose)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])
        self.cfg.refresh_from_db()
        self.assertEqual(self.cfg.initial_camera['p'], [0.0, 1.5, -7.0])
        self.assertEqual(self.cfg.initial_camera['q'], [0.0, 1.0, 0.0, 0.0])

    def test_non_manager_cannot_save(self):
        self.client.force_login(self.viewer)
        r = self._post(self.pose)
        self.assertEqual(r.status_code, 404)
        self.cfg.refresh_from_db()
        self.assertIsNone(self.cfg.initial_camera)

    def test_anonymous_redirected_to_login(self):
        r = self._post(self.pose)
        self.assertEqual(r.status_code, 302)

    def test_missing_position_rejected(self):
        self.client.force_login(self.staff)
        r = self._post({'q': [0, 1, 0, 0]})
        self.assertEqual(r.status_code, 400)

    def test_saved_pose_appears_in_viewer_config(self):
        from gallery.views.room import _config_dict
        self.cfg.initial_camera = self.pose
        self.cfg.save(update_fields=['initial_camera'])
        self.assertEqual(_config_dict(self.cfg)['initial_camera'], self.pose)


class RoomTwoDViewTests(TestCase):
    """Read-only 2D layout viewer (artists checking where to install)."""

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff2d@example.com', email='staff2d@example.com', password='pw'
        )
        add_staff_role(self.staff_user)
        self.show = Show.objects.create(
            name='TwoD Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        self.artwork = Artwork.objects.create(
            name='Painting', created_by=self.staff_user, end_year=2025,
            width_inches=20, height_inches=30,
        )
        self.show.artworks.add(self.artwork)
        from gallery.models import WallPlacement
        WallPlacement.objects.create(
            show=self.show, artwork=self.artwork, wall='N',
            x_in=0.0, y_in=48.0, z_in=0.0,
        )
        self.url = reverse('gallery:room_2d', kwargs={'slug': self.show.slug})

    def test_artwork_json_includes_hang_drop(self):
        from gallery.views.room import _artwork_json
        self.assertIsNone(_artwork_json(self.artwork)['hang_drop'])
        self.artwork.hang_drop_inches = 4.5
        self.artwork.save(update_fields=['hang_drop_inches'])
        self.assertEqual(_artwork_json(self.artwork)['hang_drop'], 4.5)

    def test_artwork_json_uses_framed_dimensions_when_set(self):
        from gallery.views.room import _artwork_json
        self.artwork.width_inches = 24
        self.artwork.height_inches = 36
        self.artwork.depth_inches = 2
        self.artwork.save()
        j = _artwork_json(self.artwork)
        self.assertEqual((j['w_in'], j['h_in'], j['d_in']), (24.0, 36.0, 2.0))  # backward compatible
        self.artwork.framed_width_inches = 30
        self.artwork.framed_height_inches = 42
        self.artwork.framed_depth_inches = 3
        self.artwork.save()
        j = _artwork_json(self.artwork)
        self.assertEqual((j['w_in'], j['h_in'], j['d_in']), (30.0, 42.0, 3.0))  # framed overrides

    def test_effective_dimensions_fallback(self):
        self.artwork.width_inches = 10
        self.artwork.height_inches = 12
        self.artwork.depth_inches = 1
        self.artwork.framed_width_inches = 15   # only width framed
        self.artwork.save()
        self.assertEqual(self.artwork.effective_width_inches, 15)   # framed used
        self.assertEqual(self.artwork.effective_height_inches, 12)  # falls back
        self.assertEqual(self.artwork.effective_depth_inches, 1)    # falls back

    def test_published_show_is_public_and_readonly(self):
        self.show.status = Show.STATUS_PUBLISHED
        self.show.save()
        resp = self.client.get(self.url)                 # anonymous
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('window.LAYOUT_READONLY = true', body)
        self.assertIn('class="readonly"', body)          # editing chrome hidden

    def test_draft_show_hidden_from_public(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_curator_can_view_draft(self):
        self.show.status = Show.STATUS_DRAFT
        self.show.save()
        self.client.force_login(self.staff_user)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_editor_is_not_readonly(self):
        self.client.force_login(self.staff_user)
        resp = self.client.get(reverse('gallery:room_layout', kwargs={'slug': self.show.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('window.LAYOUT_READONLY = false', resp.content.decode())


class ArtScheduleTests(TestCase):
    """Drop-off / pickup windows, artist scheduling, and curator check-off."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='sched-staff@example.com', email='sched-staff@example.com', password='pw')
        add_staff_role(self.staff)
        self.artist_user = User.objects.create_user(
            username='sched-artist@example.com', email='sched-artist@example.com', password='pw')
        self.artist = Artist.objects.create(
            user=self.artist_user, name='Sched Artist', first_name='Sched', last_name='Artist',
            email='sched-artist@example.com', phone='')
        self.show = Show.objects.create(
            name='Sched Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7))
        self.artwork = Artwork.objects.create(name='Piece', created_by=self.artist_user, end_year=2025)
        self.artwork.artists.add(self.artist)
        self.show.artworks.add(self.artwork)

    def test_window_schedule_and_checkoff_flow(self):
        from gallery.models import ScheduleWindow, ArtistSchedule
        # Default show is self-install → the arrival kind is 'install'.
        self.client.force_login(self.staff)
        self.client.post(reverse('gallery:show_schedule_windows', kwargs={'slug': self.show.slug}),
                         {'action': 'add', 'kind': 'install',
                          'install-date': '2025-06-07', 'install-start': '10:00', 'install-end': '14:00'})
        window = ScheduleWindow.objects.get(show=self.show, kind='install')

        # Artist schedules a time within the window
        self.client.force_login(self.artist_user)
        self.client.post(reverse('gallery:artist_schedule', kwargs={'slug': self.show.slug}),
                         {'kind': 'install', 'install-window': window.pk, 'install-time': '11:30'})
        sched = ArtistSchedule.objects.get(show=self.show, artist=self.artist, kind='install')
        self.assertEqual(sched.window_id, window.pk)
        self.assertEqual(sched.scheduled_time.strftime('%H:%M'), '11:30')
        self.assertFalse(sched.done)

        # Curator checks it off
        self.client.force_login(self.staff)
        self.client.post(reverse('gallery:show_schedule_tracker', kwargs={'slug': self.show.slug}),
                         {'artist_id': self.artist.id, 'kind': 'install', 'done': '1'})
        sched.refresh_from_db()
        self.assertTrue(sched.done)
        self.assertEqual(sched.done_by, self.staff)

    def test_curator_install_uses_dropoff(self):
        from gallery.models import ScheduleWindow, ArtistSchedule
        self.show.self_install = False   # curator installs → artists drop off
        self.show.save(update_fields=['self_install'])
        window = ScheduleWindow.objects.create(
            show=self.show, kind='dropoff', date='2025-06-07', start='10:00', end='14:00')
        self.client.force_login(self.artist_user)
        # 'install' is not a valid kind for a curator-install show → rejected
        self.client.post(reverse('gallery:artist_schedule', kwargs={'slug': self.show.slug}),
                         {'kind': 'install', 'install-window': window.pk, 'install-time': '11:00'})
        self.assertFalse(ArtistSchedule.objects.filter(show=self.show, artist=self.artist, kind='install').exists())
        # 'dropoff' works
        self.client.post(reverse('gallery:artist_schedule', kwargs={'slug': self.show.slug}),
                         {'kind': 'dropoff', 'dropoff-window': window.pk, 'dropoff-time': '11:00'})
        self.assertTrue(ArtistSchedule.objects.filter(show=self.show, artist=self.artist, kind='dropoff').exists())

    def test_time_outside_window_rejected(self):
        from gallery.models import ScheduleWindow, ArtistSchedule
        window = ScheduleWindow.objects.create(
            show=self.show, kind='install', date='2025-06-07', start='10:00', end='14:00')
        self.client.force_login(self.artist_user)
        self.client.post(reverse('gallery:artist_schedule', kwargs={'slug': self.show.slug}),
                         {'kind': 'install', 'install-window': window.pk, 'install-time': '16:00'})
        self.assertFalse(ArtistSchedule.objects.filter(show=self.show, artist=self.artist).exists())

    def test_non_participant_cannot_schedule(self):
        other = User.objects.create_user(
            username='outsider@example.com', email='outsider@example.com', password='pw')
        self.client.force_login(other)
        r = self.client.get(reverse('gallery:artist_schedule', kwargs={'slug': self.show.slug}))
        self.assertEqual(r.status_code, 404)

    def test_pickup_uses_same_mechanism(self):
        from gallery.models import ScheduleWindow, ArtistSchedule
        window = ScheduleWindow.objects.create(
            show=self.show, kind='pickup', date='2025-07-01', start='12:00', end='16:00')
        self.client.force_login(self.artist_user)
        self.client.post(reverse('gallery:artist_schedule', kwargs={'slug': self.show.slug}),
                         {'kind': 'pickup', 'pickup-window': window.pk, 'pickup-time': '13:00'})
        self.assertTrue(ArtistSchedule.objects.filter(
            show=self.show, artist=self.artist, kind='pickup').exists())

    def test_google_calendar_url(self):
        from gallery.models import ScheduleWindow, ArtistSchedule
        w = ScheduleWindow.objects.create(
            show=self.show, kind='install',
            date=datetime.date(2025, 6, 7), start=datetime.time(10, 0), end=datetime.time(14, 0))
        s = ArtistSchedule.objects.create(
            show=self.show, artist=self.artist, kind='install',
            window=w, scheduled_time=datetime.time(11, 30))
        url = s.google_calendar_url()
        self.assertIn('calendar.google.com', url)
        self.assertIn('20250607T113000', url)   # start
        self.assertIn('20250607T120000', url)   # +30 min end
        self.assertIn('Install', url)
        # Not-yet-scheduled → no URL
        s2 = ArtistSchedule.objects.create(show=self.show, artist=self.artist, kind='pickup')
        self.assertIsNone(s2.google_calendar_url())

    def test_window_google_calendar_url(self):
        from gallery.models import ScheduleWindow
        w = ScheduleWindow.objects.create(
            show=self.show, kind='install',
            date=datetime.date(2025, 6, 7), start=datetime.time(10, 0), end=datetime.time(14, 0))
        url = w.google_calendar_url()
        self.assertIn('calendar.google.com', url)
        self.assertIn('20250607T100000', url)   # window start
        self.assertIn('20250607T140000', url)   # window end
        self.assertIn('Install', url)

    def test_ics_download(self):
        from gallery.models import ScheduleWindow, ArtistSchedule
        w = ScheduleWindow.objects.create(
            show=self.show, kind='install',
            date=datetime.date(2025, 6, 7), start=datetime.time(10, 0), end=datetime.time(14, 0))
        s = ArtistSchedule.objects.create(
            show=self.show, artist=self.artist, kind='install',
            window=w, scheduled_time=datetime.time(11, 30))
        # Owner can download; response is a calendar file with the event
        self.client.force_login(self.artist_user)
        r = self.client.get(reverse('gallery:schedule_ics', kwargs={'pk': s.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/calendar', r['Content-Type'])
        body = r.content.decode()
        self.assertIn('BEGIN:VEVENT', body)
        self.assertIn('DTSTART:20250607T113000', body)
        self.assertIn('SUMMARY:Install', body)
        # A stranger cannot download it
        other = User.objects.create_user(username='ics-out@example.com', email='ics-out@example.com', password='pw')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('gallery:schedule_ics', kwargs={'pk': s.pk})).status_code, 404)


class RemoveArtworkFromShowTests(TestCase):
    """Curator/admin removes an artwork from a published show without deleting it."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='rm-staff@example.com', email='rm-staff@example.com', password='pw')
        add_staff_role(self.staff)
        self.artist_user = User.objects.create_user(
            username='rm-artist@example.com', email='rm-artist@example.com', password='pw')
        self.artist = Artist.objects.create(
            user=self.artist_user, name='RM Artist', first_name='RM', last_name='Artist',
            email='rm-artist@example.com', phone='')
        self.show = Show.objects.create(
            name='RM Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
            status=Show.STATUS_PUBLISHED)
        self.artwork = Artwork.objects.create(name='RM Piece', created_by=self.artist_user, end_year=2025)
        self.artwork.artists.add(self.artist)
        self.show.artworks.add(self.artwork)
        self.sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            status=ArtworkSubmission.ACCEPTED, curator_decision=ArtworkSubmission.CURATOR_SELECTED)

    def test_curator_removes_artwork_keeps_records(self):
        self.client.force_login(self.staff)
        r = self.client.post(reverse('gallery:remove_artwork_from_show',
                                     kwargs={'slug': self.show.slug, 'pk': self.artwork.pk}))
        self.assertEqual(r.status_code, 302)
        # No longer in the show
        self.assertFalse(self.show.artworks.filter(pk=self.artwork.pk).exists())
        # Artwork and artist still exist
        self.assertTrue(Artwork.objects.filter(pk=self.artwork.pk).exists())
        self.assertTrue(Artist.objects.filter(pk=self.artist.pk).exists())
        # Marked withdrawn (not undecided) so it's findable/restorable and a later
        # sync won't re-add it
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.curator_decision, ArtworkSubmission.WITHDRAWN)

    def test_withdrawn_piece_can_be_re_added(self):
        self.client.force_login(self.staff)
        # withdraw it
        self.client.post(reverse('gallery:remove_artwork_from_show',
                                 kwargs={'slug': self.show.slug, 'pk': self.artwork.pk}))
        self.assertFalse(self.show.artworks.filter(pk=self.artwork.pk).exists())
        # re-add via the Withdrawn section's "Re-add to show" (decision=selected)
        r = self.client.post(reverse('gallery:update_submission_status', kwargs={'pk': self.sub.pk}),
                             data={'decision': 'selected'})
        self.assertEqual(r.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.curator_decision, ArtworkSubmission.CURATOR_SELECTED)
        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())

    def test_withdrawn_shows_in_submissions_page(self):
        self.client.force_login(self.staff)
        self.sub.curator_decision = ArtworkSubmission.WITHDRAWN
        self.sub.save()
        body = self.client.get(reverse('gallery:show_submissions',
                                       kwargs={'slug': self.show.slug})).content.decode()
        self.assertIn('Withdrawn', body)
        self.assertIn('Re-add to show', body)

    def test_bulk_withdraw_from_submissions_page(self):
        import json
        self.client.force_login(self.staff)
        r = self.client.post(
            reverse('gallery:bulk_submission_status'),
            data=json.dumps({'pks': [self.sub.pk], 'decision': 'withdrawn'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.curator_decision, ArtworkSubmission.WITHDRAWN)
        self.assertFalse(self.show.artworks.filter(pk=self.artwork.pk).exists())

    def test_non_manager_cannot_remove(self):
        self.client.force_login(self.artist_user)
        r = self.client.post(reverse('gallery:remove_artwork_from_show',
                                     kwargs={'slug': self.show.slug, 'pk': self.artwork.pk}))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())


class AcceptanceEmailScheduleLinkTests(TestCase):
    """Acceptance email links to the artist scheduling page using the show's site website."""

    def setUp(self):
        self.artist_user = User.objects.create_user(
            username='ae-artist@example.com', email='ae-artist@example.com', password='pw')
        self.artist = Artist.objects.create(
            user=self.artist_user, name='AE Artist', first_name='AE', last_name='Artist',
            email='ae-artist@example.com', phone='')
        self.site = Site.objects.create(name='120710 AE', website='https://www.example-gallery.art/')
        self.show = Show.objects.create(
            name='AE Show', start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7))
        self.show.sites.add(self.site)
        self.artwork = Artwork.objects.create(name='AE Piece', created_by=self.artist_user, end_year=2025)
        self.artwork.artists.add(self.artist)
        self.sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            status=ArtworkSubmission.ACCEPTED, curator_decision=ArtworkSubmission.CURATOR_SELECTED)

    def test_acceptance_email_has_schedule_link(self):
        from gallery.views.open_call import _send_selection_email
        from django.urls import reverse
        _send_selection_email(self.sub, accepted=True)
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        expected = 'https://www.example-gallery.art' + reverse(
            'gallery:artist_schedule', kwargs={'slug': self.show.slug})
        self.assertIn(expected, html)

    def test_rejection_email_has_no_schedule_link(self):
        from gallery.views.open_call import _send_selection_email
        _send_selection_email(self.sub, accepted=False)
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('/schedule/', html)


class ArtworkLayoutImageTests(TestCase):
    """The layout/3D image is used when set, otherwise the hero image."""

    def setUp(self):
        from PIL import Image as PILImage
        self._tmp = tempfile.mkdtemp()
        d = os.path.join(self._tmp, 'artwork_images')
        os.makedirs(d)
        PILImage.new('RGB', (8, 8), 'white').save(os.path.join(d, 'hero.jpg'), 'JPEG')
        PILImage.new('RGB', (8, 8), 'black').save(os.path.join(d, 'crop.jpg'), 'JPEG')
        self._override = self.settings(MEDIA_ROOT=self._tmp)
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_layout_image_preferred_in_room_json(self):
        from gallery.views.room import _artwork_json
        a = Artwork.objects.create(name='LI', end_year=2025)
        a.image.name = 'artwork_images/hero.jpg'
        a.save()
        hero = _artwork_json(a)
        self.assertTrue(hero['img'])    # falls back to hero
        self.assertTrue(hero['thumb'])
        a.layout_image.name = 'artwork_images/crop.jpg'
        a.save()
        crop = _artwork_json(a)
        self.assertTrue(crop['img'])
        self.assertNotEqual(crop['img'], hero['img'])       # wall/3D uses the crop
        self.assertNotEqual(crop['thumb'], hero['thumb'])   # sidebar pool uses the crop thumbnail

    def test_layout_url_properties_fall_back_to_hero(self):
        a = Artwork.objects.create(name='LP', end_year=2025)
        a.image.name = 'artwork_images/hero.jpg'
        a.save()
        hero_display, hero_thumb = a.layout_display_url, a.layout_thumb_url
        self.assertTrue(hero_display)   # no crop -> hero
        self.assertTrue(hero_thumb)
        a.layout_image.name = 'artwork_images/crop.jpg'
        a.save()
        self.assertNotEqual(a.layout_display_url, hero_display)   # crop overrides hero
        self.assertNotEqual(a.layout_thumb_url, hero_thumb)

    def test_form_includes_layout_image(self):
        from gallery.forms import ArtworkForm
        self.assertIn('layout_image', ArtworkForm.base_fields)


class SubmissionAreaTests(TestCase):
    """Flagging submissions from outside the show's allowed area.

    The check never blocks a submission — every assertion here is about what the curator
    is told, not about what an artist is allowed to do.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name='Area Test Venue', country='US',
            submission_zipcodes='94710 94702\n94110, 94609',
            submission_area_label='Bay Area',
        )
        self.show = Show.objects.create(
            name='Area Test Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=30),
            submission_type=Show.SUBMISSION_OPEN,
            status=Show.STATUS_OPEN_CALL,
        )
        self.show.sites.add(self.site)

    def _artist(self, **kwargs):
        fields = dict(name='A', first_name='A', last_name='B', email='a@example.com', phone='')
        fields.update(kwargs)
        return Artist.objects.create(**fields)

    # --- Parsing ---

    def test_catchment_accepts_spaces_commas_and_newlines(self):
        codes, label = submission_area.site_catchment(self.show)
        self.assertEqual(codes, {'94710', '94702', '94110', '94609'})
        self.assertEqual(label, 'Bay Area')

    def test_notes_in_the_catchment_are_not_treated_as_postal_codes(self):
        self.site.submission_zipcodes = '94710  # dropped 94132, wrong side of the bay'
        self.site.save()
        codes, _ = submission_area.site_catchment(self.show)
        self.assertEqual(codes, {'94710'})

    def test_zip_plus_four_matches_its_five_digit_code(self):
        artist = self._artist(country='US', zipcode='94710-1234')
        self.assertEqual(submission_area.check_artist(self.show, artist),
                         submission_area.IN_AREA)

    # --- Local scope ---

    def test_local_artist_is_in_area(self):
        artist = self._artist(country='US', zipcode='94702')
        self.assertEqual(submission_area.check_artist(self.show, artist),
                         submission_area.IN_AREA)

    def test_distant_domestic_artist_is_out_of_area(self):
        artist = self._artist(country='US', zipcode='97205')
        self.assertEqual(submission_area.check_artist(self.show, artist),
                         submission_area.OUT_OF_AREA)

    def test_foreign_artist_is_out_of_area_not_unknown(self):
        """A UK postcode cannot be in a US catchment, so this is a fact, not a shrug."""
        artist = self._artist(country='GB', zipcode='EC1V 9BD')
        self.assertEqual(submission_area.check_artist(self.show, artist),
                         submission_area.OUT_OF_AREA)

    def test_missing_zipcode_is_unknown(self):
        artist = self._artist(country='US', zipcode='')
        self.assertEqual(submission_area.check_artist(self.show, artist),
                         submission_area.UNKNOWN)

    def test_unconfigured_catchment_says_nothing(self):
        """No badge at all, rather than quietly asserting everyone is local."""
        self.site.submission_zipcodes = ''
        self.site.save()
        artist = self._artist(country='US', zipcode='97205')
        self.assertIsNone(submission_area.check_artist(self.show, artist))

    # --- National and anywhere ---

    def test_national_show_compares_countries(self):
        self.show.submission_scope = Show.SCOPE_NATIONAL
        self.show.save()
        self.assertEqual(
            submission_area.check_artist(self.show, self._artist(country='US', zipcode='97205')),
            submission_area.IN_AREA)
        self.assertEqual(
            submission_area.check_artist(self.show, self._artist(country='GB', zipcode='')),
            submission_area.OUT_OF_AREA)

    def test_anywhere_show_never_flags(self):
        self.show.submission_scope = Show.SCOPE_ANYWHERE
        self.show.save()
        artist = self._artist(country='GB', zipcode='EC1V 9BD')
        self.assertIsNone(submission_area.check_artist(self.show, artist))

    # --- Labels ---

    def test_description_names_the_area_and_where_they_are(self):
        artist = self._artist(country='GB', zipcode='EC1V 9BD')
        text = submission_area.describe(
            self.show, artist, submission_area.OUT_OF_AREA)
        self.assertEqual(text, 'Outside Bay Area · EC1V 9BD · United Kingdom')

    def test_description_omits_the_country_when_it_matches_the_venue(self):
        """"United States of America" on every badge of a Berkeley show is pure noise."""
        artist = self._artist(country='US', zipcode='97205')
        text = submission_area.describe(
            self.show, artist, submission_area.OUT_OF_AREA)
        self.assertEqual(text, 'Outside Bay Area · 97205')

    def test_blind_review_withholds_the_location(self):
        """A location identifies an artist the same way a name does."""
        artist = self._artist(country='GB', zipcode='EC1V 9BD')
        text = submission_area.describe(
            self.show, artist, submission_area.OUT_OF_AREA, blind=True)
        self.assertEqual(text, 'Outside area')
        self.assertNotIn('EC1V', text)

    def test_in_area_artists_get_no_label(self):
        artist = self._artist(country='US', zipcode='94710')
        self.assertEqual(
            submission_area.describe(self.show, artist, submission_area.IN_AREA), '')


class SubmissionAreaCurationTests(TestCase):
    """The out-of-area flag as a curator actually meets it, on the submissions page."""

    def setUp(self):
        self.site = Site.objects.create(
            name='Curation Area Venue', country='US',
            submission_zipcodes='94710', submission_area_label='Bay Area',
        )
        self.curator_user = User.objects.create_user(
            username='cur@example.com', email='cur@example.com', password='pw')
        self.curator = Artist.objects.create(
            user=self.curator_user, name='Cur', first_name='Cur', last_name='C',
            email='cur@example.com', phone='')
        self.show = Show.objects.create(
            name='Curation Area Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=30),
            submission_type=Show.SUBMISSION_OPEN,
            status=Show.STATUS_OPEN_CALL,
        )
        self.show.sites.add(self.site)
        self.show.curators.add(self.curator)

        self.local = self._submit('Local Piece', '94710', 'US')
        self.distant = self._submit('Distant Piece', '97205', 'US')
        self.nowhere = self._submit('Unplaced Piece', '', 'US')
        self.client.force_login(self.curator_user)

    def _submit(self, title, zipcode, country):
        user = User.objects.create_user(
            username=f'{title}@example.com', email=f'{title}@example.com', password='pw')
        artist = Artist.objects.create(
            user=user, name=title, first_name=title, last_name='X',
            email=f'{title}@example.com', phone='', country=country, zipcode=zipcode)
        artwork = Artwork.objects.create(name=title, created_by=user, end_year=2025)
        artwork.artists.add(artist)
        return ArtworkSubmission.objects.create(
            show=self.show, artwork=artwork, submitted_by=user)

    def _get(self, query=''):
        return self.client.get(
            reverse('gallery:show_submissions', kwargs={'slug': self.show.slug}) + query)

    def test_page_counts_out_of_area_submissions(self):
        response = self._get()
        self.assertEqual(response.context['n_out_of_area'], 1)
        self.assertContains(response, 'outside area')

    def test_page_counts_and_links_submissions_with_no_location(self):
        response = self._get()
        self.assertEqual(response.context['n_unplaced'], 1)
        self.assertContains(response, 'location not given')
        self.assertContains(response, 'area=unknown')

    def test_out_of_area_filter_narrows_to_flagged_submissions(self):
        response = self._get('?area=out')
        titles = {s.artwork.name for s in response.context['submissions']}
        self.assertEqual(titles, {'Distant Piece'})

    def test_unknown_filter_finds_submissions_with_no_location(self):
        response = self._get('?area=unknown')
        titles = {s.artwork.name for s in response.context['submissions']}
        self.assertEqual(titles, {'Unplaced Piece'})

    def test_blind_review_keeps_the_flag_but_drops_the_detail(self):
        self.show.blind_review = True
        self.show.save()
        response = self._get()
        self.assertContains(response, 'Outside area')
        self.assertNotContains(response, '97205')
        self.assertNotContains(response, 'Distant Piece &middot;')

    def test_area_check_does_not_add_queries_per_submission(self):
        """The catchment is loaded once per page, not once per card.

        The first version called `show.sites.all()` inside the loop, which took a
        60-submission page from 13 queries to 193. Asserting the count is flat between two
        page sizes catches that without hard-coding a number that ordinary changes to this
        view would invalidate.
        """
        def queries_for(extra):
            for i in range(extra):
                self._submit(f'Filler {i}', '94710', 'US')
            with CaptureQueriesContext(connection) as ctx:
                self._get()
            return len(ctx)

        few = queries_for(0)        # the three submissions from setUp
        many = queries_for(20)
        self.assertEqual(few, many,
                         f'{few} queries for 3 submissions, {many} for 23 — '
                         f'the area check is querying per submission')

    def test_flag_disappears_when_the_show_accepts_work_from_anywhere(self):
        self.show.submission_scope = Show.SCOPE_ANYWHERE
        self.show.save()
        response = self._get()
        self.assertEqual(response.context['n_out_of_area'], 0)
        self.assertNotContains(response, 'outside area')


class SitePublicInfoTests(TestCase):
    """The Info / Visit / Contact / Links pages, read from the Site rather than hard-coded.

    The point of these is the second gallery: every assertion here is about a venue
    getting *its own* content, or degrading sensibly when it has none.
    """

    def setUp(self):
        self.gallery = Site.objects.create(
            name='First Gallery', status=Site.STATUS_PUBLISHED,
            street='1207 Tenth Street', city='Berkeley', state='CA',
            postal_code='94710', country='US',
            email='hello@first.example', phone='555-0100', instagram='@first',
            hours='Sun 1-4p', about='<h1>Mission</h1><p>First gallery mission.</p>',
            visit_notes='<p>Street parking available</p>',
            latitude='37.881613', longitude='-122.297071',
        )
        self.other = Site.objects.create(
            name='Second Gallery', status=Site.STATUS_PUBLISHED,
            city='Oakland', state='CA', country='US',
            about='<p>Second gallery mission.</p>',
        )
        self.bare = Site.objects.create(
            name='Bare Gallery', status=Site.STATUS_PUBLISHED, country='US')

    def _page(self, page, site=None):
        if site is None:
            return self.client.get(reverse(page))
        return self.client.get(reverse(f'site_{page}', kwargs={'site_slug': site.slug}))

    # --- Each venue gets its own ---

    def test_each_venue_shows_its_own_about(self):
        first = self._page('about', self.gallery)
        second = self._page('about', self.other)
        self.assertContains(first, 'First gallery mission.')
        self.assertNotContains(first, 'Second gallery mission.')
        self.assertContains(second, 'Second gallery mission.')
        self.assertNotContains(second, 'First gallery mission.')

    def test_visit_shows_the_venues_own_address_and_hours(self):
        response = self._page('visit', self.gallery)
        self.assertContains(response, '1207 Tenth Street')
        self.assertContains(response, 'Sun 1-4p')
        self.assertContains(response, 'Street parking available')

    def test_contact_shows_the_venues_own_details(self):
        """Its address and hours, but no longer an email address or a phone number.

        Those were removed deliberately: the gallery is reached by booking a visit, by the
        mailing list, or by an enquiry on a work — each of which arrives somewhere it can be
        dealt with. Publishing an address also hands it to every scraper that reads the page.
        """
        response = self._page('contact', self.gallery)
        self.assertContains(response, self.gallery.name)
        self.assertNotContains(response, 'hello@first.example')
        self.assertNotContains(response, '555-0100')

    def test_map_link_comes_from_the_venues_coordinates(self):
        """Generated, not a committed screenshot of one gallery."""
        response = self._page('visit', self.gallery)
        self.assertContains(response, '37.881613,-122.297071')

    # --- Degrading when a venue has nothing ---

    def test_a_venue_with_no_details_omits_the_sections(self):
        """Silence, not an empty mailto: link or a bare address block."""
        response = self._page('contact', self.bare)
        # The labels, not "mailto:" — base.html's footer carries a commented-out one.
        self.assertNotContains(response, '<b>email:</b>')
        self.assertNotContains(response, '<b>phone:</b>')
        self.assertNotContains(response, '<b>address:</b>')
        self.assertNotContains(response, 'Gallery Hours')

    def test_a_venue_with_no_about_says_so(self):
        response = self._page('about', self.bare)
        self.assertContains(response, 'No information has been added yet')

    def test_about_falls_back_to_the_description(self):
        self.bare.description = 'A short description.'
        self.bare.save()
        response = self._page('about', self.bare)
        self.assertContains(response, 'A short description.')

    def test_about_markup_is_sanitized(self):
        self.bare.about = '<h1>Fine</h1><script>bad()</script><img src="/x.png" onerror="pwn()">'
        self.bare.save()
        response = self._page('about', self.bare)
        self.assertContains(response, '<h1>Fine</h1>')       # headings and tables survive
        self.assertContains(response, '<img src="/x.png">')  # the image, minus the handler
        # Assert on the payloads, not on "<script>" — base.html has script tags of its own.
        self.assertNotContains(response, 'bad()')
        self.assertNotContains(response, 'onerror')
        self.assertNotContains(response, 'pwn()')

    def test_static_references_in_about_are_resolved_per_environment(self):
        """Stored copy says /static/x; production serves hashed names from S3.

        The About content came from a template that used {% static %}. Storing the resolved
        path would have been correct in local dev and a 404 in production, where
        ManifestStaticFilesStorage hashes filenames and STATIC_URL points at CloudFront. So
        the reference is re-resolved on render — this asserts the production shape.
        """
        self.bare.about = '<p><img src="/static/img/120710-former-cal-professor.jpg"></p>'
        self.bare.save()
        with override_settings(
            STATIC_URL='https://cdn.example.com/static/',
            STORAGES={
                'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                'staticfiles': {
                    'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
            },
        ):
            response = self._page('about', self.bare)
            self.assertContains(
                response,
                'https://cdn.example.com/static/img/120710-former-cal-professor.jpg')
            self.assertNotContains(response, 'src="/static/img/120710')

    def test_an_unresolvable_static_reference_does_not_break_the_page(self):
        """A hashed-storage miss raises ValueError; a broken image beats a 500."""
        self.bare.about = '<p><img src="/static/img/deleted-long-ago.png"></p>'
        self.bare.save()
        response = self._page('about', self.bare)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/static/img/deleted-long-ago.png')

    # --- Links ---

    def test_links_shows_the_venues_own_plus_network_wide_ones(self):
        LinkTreeEntry.objects.create(name='First site', url='https://first.example',
                                     site=self.gallery)
        LinkTreeEntry.objects.create(name='Second site', url='https://second.example',
                                     site=self.other)
        LinkTreeEntry.objects.create(name='The network', url='https://network.example')
        response = self._page('linktree', self.gallery)
        self.assertContains(response, 'https://first.example')
        self.assertContains(response, 'https://network.example')   # no site = everywhere
        self.assertNotContains(response, 'https://second.example')

    def test_links_only_lists_shows_at_that_venue(self):
        mine = Show.objects.create(
            name='Mine', start=datetime.date.today() - datetime.timedelta(days=1),
            end=datetime.date.today() + datetime.timedelta(days=1),
            status=Show.STATUS_PUBLISHED)
        mine.sites.add(self.gallery)
        theirs = Show.objects.create(
            name='Theirs', start=datetime.date.today() - datetime.timedelta(days=1),
            end=datetime.date.today() + datetime.timedelta(days=1),
            status=Show.STATUS_PUBLISHED)
        theirs.sites.add(self.other)
        response = self._page('linktree', self.gallery)
        self.assertContains(response, 'Mine')
        self.assertNotContains(response, 'Theirs')

    # --- Publication ---

    def test_a_draft_venues_info_pages_are_not_public(self):
        """The context processor resolves a site from the path without checking status, so
        these four routes have to check it themselves or a hidden venue becomes readable."""
        draft = Site.objects.create(name='Draft Gallery', status=Site.STATUS_DRAFT,
                                    about='<p>Not yet public.</p>', country='US')
        for page in ('about', 'visit', 'contact', 'linktree'):
            response = self._page(page, draft)
            self.assertEqual(response.status_code, 404, page)
            self.assertNotContains(response, 'Not yet public.', status_code=404)

    # --- The network level, i.e. after the reset.art cutover ---

    def test_unscoped_pages_read_the_default_site(self):
        with override_settings(GALLERY_DEFAULT_SITE_SLUG=self.other.slug):
            import eatart.context_processors as cp
            original = cp._DEFAULT_SITE_SLUG
            cp._DEFAULT_SITE_SLUG = self.other.slug
            try:
                response = self._page('about')
                self.assertContains(response, 'Second gallery mission.')
            finally:
                cp._DEFAULT_SITE_SLUG = original

    def test_unscoped_pages_survive_having_no_default_site(self):
        """Post-cutover safety net: nothing 500s if the default site is unset or missing."""
        with override_settings(GALLERY_DEFAULT_SITE_SLUG=None):
            import eatart.context_processors as cp
            original = cp._DEFAULT_SITE_SLUG
            cp._DEFAULT_SITE_SLUG = None
            try:
                for page in ('about', 'visit', 'contact', 'linktree'):
                    self.assertEqual(self._page(page).status_code, 200, page)
            finally:
                cp._DEFAULT_SITE_SLUG = original


class CalendarTests(TestCase):
    """The merged agenda and the iCalendar feed.

    Weighted towards the things that are quietly wrong rather than obviously broken: the
    exclusive DTEND, the time zone conversion, and what a public feed must not contain.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name='Cal Venue', status=Site.STATUS_PUBLISHED, state='CA', country='US',
            street='1207 Tenth Street', city='Berkeley', postal_code='94710')
        self.other_site = Site.objects.create(
            name='Other Venue', status=Site.STATUS_PUBLISHED, state='NY', country='US')
        # Relative to today, not absolute. These were 2026-08-01/02 and were a current
        # show with an upcoming opening right up until the real clock passed them, at
        # which point the opening moved into the past section and two tests failed for
        # reasons that had nothing to do with the code.
        today = datetime.date.today()
        self.show = Show.objects.create(
            name='Summer Show', status=Show.STATUS_PUBLISHED,
            start=today - datetime.timedelta(days=2),
            end=today + datetime.timedelta(days=28))
        self.show.sites.add(self.site)
        self.event = Event.objects.create(
            show=self.show, name='Opening Reception',
            date=today + datetime.timedelta(days=1),
            start=datetime.time(18, 0), end=datetime.time(21, 0))

    def _ics(self, site=None):
        url = (reverse('gallery:site_shows_ics', kwargs={'site_slug': site.slug}) if site
               else reverse('gallery:shows_ics'))
        return self.client.get(url).content.decode()

    # --- Time zone, derived and applied ---

    def test_timezone_is_derived_from_an_unambiguous_state(self):
        self.assertEqual(self.site.timezone, 'America/Los_Angeles')
        self.assertEqual(self.other_site.timezone, 'America/New_York')

    def test_timezone_is_left_blank_for_a_split_state(self):
        """Florida is Eastern except the western panhandle. A guess would be an hour wrong."""
        split = Site.objects.create(name='Panhandle', state='FL', country='US',
                                    status=Site.STATUS_PUBLISHED)
        self.assertEqual(split.timezone, '')

    def test_an_explicit_timezone_survives_an_address_edit(self):
        self.site.timezone = 'America/Phoenix'
        self.site.save()
        self.site.state = 'CA'
        self.site.save()
        self.site.refresh_from_db()
        self.assertEqual(self.site.timezone, 'America/Phoenix')

    def test_event_times_are_published_as_utc_instants(self):
        """18:00 in Berkeley in August is PDT, so 01:00Z the following day.

        Pins its own date rather than using the fixture's: this asserts one exact instant,
        so it needs a known day, while the listing tests need one relative to today."""
        self.event.date = datetime.date(2026, 8, 2)
        self.event.save(update_fields=['date'])
        self.assertIn('DTSTART:20260803T010000Z', self._ics())
        self.assertIn('DTEND:20260803T040000Z', self._ics())

    def test_daylight_saving_is_applied_per_date(self):
        """The same wall-clock time is a different instant in winter — hence zoneinfo."""
        winter = Show.objects.create(
            name='Winter Show', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2027, 1, 10), end=datetime.date(2027, 1, 20))
        winter.sites.add(self.site)
        Event.objects.create(show=winter, name='Winter Opening',
                             date=datetime.date(2027, 1, 11),
                             start=datetime.time(18, 0), end=datetime.time(21, 0))
        self.event.date = datetime.date(2026, 8, 2)      # a known summer date, see above
        self.event.save(update_fields=['date'])
        body = self._ics()
        self.assertIn('DTSTART:20260803T010000Z', body)   # August, PDT (-7)
        self.assertIn('DTSTART:20270112T020000Z', body)   # January, PST (-8)

    # --- The exclusive DTEND ---

    def test_all_day_show_end_is_exclusive(self):
        """A show ending 31 Aug must publish DTEND 1 Sep, or clients draw it a day short.

        Derived from the fixture rather than written out: the fixture moves with today, so
        hard-coded dates here passed only while the real clock happened to agree with
        them, and then failed for a reason that had nothing to do with the code.
        """
        body = self._ics()
        self.assertIn(f'DTSTART;VALUE=DATE:{self.show.start:%Y%m%d}', body)
        day_after_the_last_day = self.show.end + datetime.timedelta(days=1)
        self.assertIn(f'DTEND;VALUE=DATE:{day_after_the_last_day:%Y%m%d}', body)

    def test_a_single_day_show_still_has_a_days_length(self):
        one_day = Show.objects.create(
            name='One Day', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 5), end=datetime.date(2026, 9, 5))
        one_day.sites.add(self.site)
        body = self._ics()
        self.assertIn('DTSTART;VALUE=DATE:20260905', body)
        self.assertIn('DTEND;VALUE=DATE:20260906', body)

    # --- What a public feed must not leak ---

    def test_unpublished_shows_are_absent_even_for_staff(self):
        """The feed URL is unauthenticated and cacheable, so it must not vary by viewer."""
        draft = Show.objects.create(
            name='Secret Show', status=Show.STATUS_DRAFT,
            start=datetime.date(2026, 8, 5), end=datetime.date(2026, 8, 20))
        draft.sites.add(self.site)
        staff = User.objects.create_user(username='s@e.com', email='s@e.com', password='p')
        add_staff_role(staff)
        self.client.force_login(staff)
        body = self._ics()
        self.assertNotIn('Secret Show', body)
        self.assertIn('Summer Show', body)

    def test_a_venues_feed_excludes_another_venues_shows(self):
        theirs = Show.objects.create(
            name='Their Show', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 8, 3), end=datetime.date(2026, 8, 9))
        theirs.sites.add(self.other_site)
        body = self._ics(site=self.site)
        self.assertIn('Summer Show', body)
        self.assertNotIn('Their Show', body)

    # --- Serving it ---

    def test_feed_is_served_as_a_subscribable_calendar(self):
        response = self.client.get(reverse('gallery:shows_ics'))
        self.assertEqual(response['Content-Type'], 'text/calendar; charset=utf-8')
        # Not an attachment: most clients save a dead snapshot instead of subscribing.
        self.assertNotIn('attachment', response['Content-Disposition'])

    def test_feed_answers_304_when_nothing_has_changed(self):
        first = self.client.get(reverse('gallery:shows_ics'))
        self.assertEqual(first.status_code, 200)
        again = self.client.get(reverse('gallery:shows_ics'),
                                HTTP_IF_MODIFIED_SINCE=first['Last-Modified'])
        self.assertEqual(again.status_code, 304)

    def test_lines_are_folded_and_crlf_terminated(self):
        self.show.name = 'A show with a deliberately very long name indeed ' * 3
        self.show.save()
        body = self._ics()
        self.assertIn('\r\n', body)
        for line in body.split('\r\n'):
            self.assertLessEqual(len(line.encode('utf-8')), 75, line[:40])
        # Folded continuations start with a single space, per RFC 5545.
        self.assertTrue(any(l.startswith(' ') for l in body.split('\r\n')))

    def test_uids_are_stable_across_regeneration(self):
        """Unstable UIDs make every client poll create duplicates instead of updating."""
        first = [l for l in self._ics().split('\r\n') if l.startswith('UID:')]
        second = [l for l in self._ics().split('\r\n') if l.startswith('UID:')]
        self.assertEqual(first, second)
        self.assertIn(f'UID:show-{self.show.pk}@testserver', first)

    # --- The agenda page ---

    def test_calendar_page_lists_shows_and_their_events(self):
        response = self.client.get(reverse('gallery:calendar'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Summer Show')
        self.assertContains(response, 'Opening Reception')

    def test_a_show_sorts_before_its_own_events_on_a_shared_date(self):
        Event.objects.create(show=self.show, name='Same Day Talk',
                             date=self.show.start,
                             start=datetime.time(19, 0), end=datetime.time(20, 0))
        entries = calendars.timeline()
        first_two = [e.kind for e in entries if e.sort_date == self.show.start]
        self.assertEqual(first_two[0], calendars.KIND_SHOW)

    def test_scoped_calendar_page_names_the_venue(self):
        response = self.client.get(
            reverse('gallery:site_calendar', kwargs={'site_slug': self.site.slug}))
        self.assertContains(response, f'Calendar at {self.site.name}')

    # --- One page, everything on it ---

    def _many_shows(self, count, first=datetime.date(2026, 9, 1)):
        """One show a day from `first`, so the run crosses month boundaries."""
        for i in range(count):
            day = first + datetime.timedelta(days=i)
            show = Show.objects.create(name=f'Show {i:03d}', status=Show.STATUS_PUBLISHED,
                                       start=day, end=day)
            show.sites.add(self.site)

    def test_every_show_is_on_the_one_page(self):
        """No pagination and no lazy loading: a reader scrolls, or uses browser search."""
        self._many_shows(60)
        response = self.client.get(reverse('gallery:calendar'))
        # 60 created, plus Summer Show and its Opening Reception from setUp.
        self.assertEqual(len(response.context['upcoming_rows']), 62)
        self.assertContains(response, 'Show 000')
        self.assertContains(response, 'Show 059')

    def test_there_is_no_pagination_or_partial_loading(self):
        """Guards against the version this replaced, which split the page in two."""
        response = self.client.get(reverse('gallery:calendar'))
        body = response.content.decode()
        self.assertNotContains(response, 'data-infinite-grid')
        self.assertNotContains(response, 'infinite-meta')
        self.assertNotContains(response, 'past=1')
        self.assertNotIn('page=2', body)

    def test_past_and_upcoming_are_both_present_without_a_switch(self):
        old = Show.objects.create(name='Ancient Show', status=Show.STATUS_PUBLISHED,
                                  start=datetime.date(2020, 1, 1),
                                  end=datetime.date(2020, 2, 1))
        old.sites.add(self.site)
        response = self.client.get(reverse('gallery:calendar'))
        self.assertContains(response, 'Summer Show')      # upcoming
        self.assertContains(response, 'Ancient Show')     # and the past, same response

    def test_upcoming_comes_before_the_past(self):
        """What is on now belongs at the top, not under the whole archive."""
        old = Show.objects.create(name='Ancient Show', status=Show.STATUS_PUBLISHED,
                                  start=datetime.date(2020, 1, 1),
                                  end=datetime.date(2020, 2, 1))
        old.sites.add(self.site)
        body = self.client.get(reverse('gallery:calendar')).content.decode()
        self.assertLess(body.index('Summer Show'), body.index('Ancient Show'))

    def test_past_runs_backwards_from_the_most_recent(self):
        for year in (2020, 2021, 2022):
            show = Show.objects.create(name=f'Show {year}', status=Show.STATUS_PUBLISHED,
                                       start=datetime.date(year, 1, 1),
                                       end=datetime.date(year, 2, 1))
            show.sites.add(self.site)
        names = [r['entry'].name
                 for r in self.client.get(reverse('gallery:calendar')).context['past_rows']]
        self.assertEqual(names, ['Show 2022', 'Show 2021', 'Show 2020'])

    def test_a_past_show_stays_above_its_own_events(self):
        """Plain reverse order splits them: an opening on the 3rd sorts above the show that
        opened on the 2nd, so the event appears above the thing it is part of."""
        old = Show.objects.create(name='Old Show', status=Show.STATUS_PUBLISHED,
                                  start=datetime.date(2020, 9, 2),
                                  end=datetime.date(2020, 12, 1))
        old.sites.add(self.site)
        Event.objects.create(show=old, name='Old Opening',
                             date=datetime.date(2020, 9, 3),
                             start=datetime.time(18, 0), end=datetime.time(21, 0))
        rows = self.client.get(reverse('gallery:calendar')).context['past_rows']
        names = [r['entry'].name for r in rows]
        self.assertLess(names.index('Old Show'), names.index('Old Opening'))

    def test_a_month_heading_appears_once_per_month(self):
        self._many_shows(40, first=datetime.date(2026, 10, 1))
        rows = self.client.get(reverse('gallery:calendar')).context['upcoming_rows']
        months = [r['month'] for r in rows if r['month']]
        self.assertEqual(len(months), len(set(months)), 'a month heading was repeated')

    # --- Event rows ---

    def test_an_event_links_to_the_show_it_belongs_to(self):
        response = self.client.get(reverse('gallery:calendar'))
        self.assertContains(response, self.show.get_absolute_url())
        self.assertContains(response, 'part of')

    def test_draft_venue_calendar_is_not_public(self):
        draft = Site.objects.create(name='Draft Venue', status=Site.STATUS_DRAFT,
                                    state='CA', country='US')
        for name in ('site_calendar', 'site_shows_ics'):
            response = self.client.get(
                reverse(f'gallery:{name}', kwargs={'site_slug': draft.slug}))
            self.assertEqual(response.status_code, 404, name)


class RobotsTests(TestCase):
    """/robots.txt used to 404.

    Which cost a full request cycle to say nothing, and left every crawler unguided over
    every URL. The logs that prompted this had SemrushBot crawling from nine IPs at once
    while real visitors waited twenty seconds behind a single worker.
    """

    def test_robots_is_served_as_plain_text(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')

    def test_it_costs_at_most_one_query(self):
        """A crawler-facing file should not do database work to answer."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/robots.txt')
        self.assertLessEqual(len(ctx), 1)

    def test_auth_and_inquiry_pages_are_disallowed(self):
        """Nothing to index, and following them burns a worker on a form."""
        body = self.client.get('/robots.txt').content.decode()
        self.assertIn('Disallow: /accounts/', body)
        self.assertIn('Disallow: /artwork/*/inquire/', body)

    def test_search_engines_that_send_readers_are_still_allowed(self):
        body = self.client.get('/robots.txt').content.decode()
        for agent in ('Googlebot', 'bingbot'):
            self.assertIn(f'User-agent: {agent}', body)
        # ...and the ones that only take are not.
        self.assertRegex(body, r'User-agent: SemrushBot\s+Disallow: /')


class SubscriberTests(TestCase):
    """One person, many lists.

    The model was first written as one row per (site, email), which made a person on two
    galleries' lists two rows with two names and two independent unsubscribe states — and
    left "stop emailing me" with nowhere single to be recorded.
    """

    def setUp(self):
        self.venue = Site.objects.create(name='Venue One', status=Site.STATUS_PUBLISHED,
                                         country='US')
        self.other = Site.objects.create(name='Venue Two', status=Site.STATUS_PUBLISHED,
                                         country='US')

    def test_one_person_on_two_lists_is_one_row(self):
        Subscriber.opt_in(email='both@example.com', first_name='Bo',
                          sites=[self.venue, self.other])
        self.assertEqual(Subscriber.objects.filter(email='both@example.com').count(), 1)
        self.assertEqual(Subscription.objects.count(), 2)

    def test_email_is_case_insensitive(self):
        Subscriber.opt_in(email='Person@Example.COM ', sites=[self.venue])
        Subscriber.opt_in(email='person@example.com', sites=[self.venue])
        self.assertEqual(Subscriber.objects.count(), 1)
        self.assertEqual(Subscription.objects.count(), 1)

    def test_unsubscribing_one_list_leaves_the_others(self):
        subscriber, subs = Subscriber.opt_in(email='a@example.com',
                                             sites=[self.venue, self.other])
        subs[0].unsubscribe()
        remaining = [s.list_name for s in subscriber.subscriptions.filter(is_subscribed=True)]
        self.assertEqual(remaining, [self.other.name])

    def test_unsubscribe_all_clears_every_list(self):
        subscriber, _ = Subscriber.opt_in(email='a@example.com',
                                          sites=[self.venue, self.other, None])
        self.assertEqual(subscriber.unsubscribe_all(), 3)
        self.assertFalse(subscriber.subscriptions.filter(is_subscribed=True).exists())

    def test_unsubscribing_twice_does_not_move_the_timestamp(self):
        _, subs = Subscriber.opt_in(email='a@example.com', sites=[self.venue])
        subs[0].unsubscribe()
        first = subs[0].unsubscribed_at
        self.assertFalse(subs[0].unsubscribe())
        subs[0].refresh_from_db()
        self.assertEqual(subs[0].unsubscribed_at, first)

    def test_opting_in_again_after_unsubscribing_is_honoured(self):
        """They asked. Refusing would be worse than honouring it."""
        subscriber, subs = Subscriber.opt_in(email='a@example.com', sites=[self.venue])
        subs[0].unsubscribe()
        Subscriber.opt_in(email='a@example.com', sites=[self.venue])
        subs[0].refresh_from_db()
        self.assertTrue(subs[0].is_subscribed)
        self.assertEqual(subs[0].unsubscribed_reason, '')

    def test_a_later_form_submission_improves_a_blank_imported_name(self):
        Subscription.objects.create(
            subscriber=Subscriber.objects.create(email='a@example.com'), site=self.venue,
            source=Subscription.SOURCE_IMPORT)
        Subscriber.opt_in(email='a@example.com', first_name='Real', last_name='Name',
                          sites=[self.venue])
        self.assertEqual(Subscriber.objects.get(email='a@example.com').full_name,
                         'Real Name')


class CampaignTests(TestCase):
    """Rendering, the send guard, and who a campaign reaches."""

    def setUp(self):
        self.site = Site.objects.create(
            name='Test Gallery', status=Site.STATUS_PUBLISHED, country='US',
            street='1207 Tenth Street', city='Berkeley', state='CA', postal_code='94710')
        self.campaign = Campaign.objects.create(
            subject='Opening night', site=self.site,
            body_markdown='# Come along\n\nOpening **Saturday**, [details](https://x.test/).')

    # --- Rendering ---

    def test_markdown_compiles_to_table_based_html(self):
        """MJML's whole job: Outlook renders with the Word engine and needs tables."""
        html = campaigns.render_preview(self.campaign)
        self.assertGreater(html.count('<table'), 3)
        self.assertIn('<strong>Saturday</strong>', html)
        self.assertIn('href="https://x.test/"', html)

    def _site_with_icon(self):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        # A real image, however small: imagekit has to be able to open and resize it, so
        # hand-written bytes are not good enough.
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'white').save(buffer, format='PNG')
        self.site.icon = SimpleUploadedFile('icon.png', buffer.getvalue(),
                                            content_type='image/png')
        self.site.save()
        return self.site

    def test_the_masthead_is_generated_if_it_was_never_made(self):
        """The cachefile strategy is Optimistic: derived images are made when the source is saved.

        icon_md was added long after these icons were uploaded, so it had never been generated —
        and because Optimistic deliberately skips existence checks on .url, every campaign went out
        with a broken image and nothing noticed.
        """
        campaigns._LOGO_URL.clear()
        site = self._site_with_icon()
        spec = site.icon_md
        spec.storage.delete(spec.name)
        self.assertFalse(spec.storage.exists(spec.name))

        url = campaigns.campaign_logo_url(site)
        self.assertTrue(url)
        self.assertTrue(spec.storage.exists(spec.name), 'the masthead should have been generated')

    def test_each_venue_gets_its_own_masthead(self):
        """Not a fix for one gallery: every venue's own logo, generated on demand for each."""
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        from gallery.models import Site

        campaigns._LOGO_URL.clear()
        urls = {}
        for slug, colour in (('120710', 'white'), ('elsewhere', 'black')):
            site = (self.site if slug == '120710'
                    else Site.objects.create(name='Elsewhere', slug=slug,
                                             status=Site.STATUS_PUBLISHED))
            buffer = io.BytesIO()
            Image.new('RGB', (8, 8), colour).save(buffer, format='PNG')
            site.icon = SimpleUploadedFile(f'{slug}.png', buffer.getvalue(),
                                           content_type='image/png')
            site.save()

            url = campaigns.campaign_logo_url(site)
            self.assertTrue(url, f'{slug} got no masthead')
            self.assertTrue(site.icon_md.storage.exists(site.icon_md.name))
            urls[slug] = url

        self.assertNotEqual(urls['120710'], urls['elsewhere'],
                            'each venue must get its own image, not a shared one')

    def test_a_venue_with_no_icon_shows_its_name_rather_than_a_stranger_logo(self):
        from gallery.models import Site
        campaigns._LOGO_URL.clear()
        bare = Site.objects.create(name='Bare', slug='bare-logo',
                                   status=Site.STATUS_PUBLISHED)
        self.assertEqual(campaigns.campaign_logo_url(bare), '')

    def test_the_masthead_url_is_absolute(self):
        """A relative /media/... resolves against the mail client, not against us."""
        campaigns._LOGO_URL.clear()
        url = campaigns.campaign_logo_url(self._site_with_icon())
        self.assertTrue(url.startswith('http://') or url.startswith('https://'), url)

    def test_a_masthead_that_cannot_be_made_falls_back_to_the_wordmark(self):
        """A broken image cannot be fixed once the mail is sent, so no image is the safer failure."""
        from unittest import mock
        campaigns._LOGO_URL.clear()
        site = self._site_with_icon()
        spec = site.icon_md
        spec.storage.delete(spec.name)
        campaign = Campaign.objects.create(
            site=site, subject='Hello', body_markdown='Body.')

        with mock.patch('imagekit.cachefiles.ImageCacheFile.generate',
                        side_effect=RuntimeError('storage down')):
            campaigns._LOGO_URL.clear()
            self.assertEqual(campaigns.campaign_logo_url(site), '')
            campaigns._LOGO_URL.clear()
            html = campaigns.render_preview(campaign)

        # No masthead image, and the venue's name carries the identity instead.
        self.assertNotIn('site_icons', html)
        self.assertIn('120710', html)

    def test_the_masthead_is_resolved_once_rather_than_once_per_recipient(self):
        """Optimistic exists to avoid an S3 request per image; a check per message would undo it.

        Proven by seeding the cache and watching the next call honour it, rather than by counting
        calls into imagekit — which storage backend does the existence check is an implementation
        detail this should not be pinned to.
        """
        campaigns._LOGO_URL.clear()
        site = self._site_with_icon()
        first = campaigns.campaign_logo_url(site)
        self.assertTrue(first)

        campaigns._LOGO_URL[(site.pk, site.icon.name)] = 'https://example.test/cached.png'
        self.assertEqual(campaigns.campaign_logo_url(site), 'https://example.test/cached.png')

    def test_no_email_reaches_out_to_a_third_party_for_a_font(self):
        """MJML adds a <link> to fonts.googleapis.com if it recognises a Google font name.

        Caught by accident: adding Roboto to the font stack made an unrelated list assertion
        fail, because "<li" also matches "<link". A Google Fonts tag would make every email
        fetch a resource from Google on open, which is a tracking vector and contradicts what
        the privacy page promises.
        """
        html = campaigns.render_preview(self.campaign)
        self.assertNotIn('fonts.googleapis.com', html)
        self.assertNotIn('<link', html)

    def test_lists_become_real_lists(self):
        """The construct a newsletter author reaches for most, and the one that used to fail.

        Dash bullets rendered as literal "- " text, and star bullets were worse: the emphasis
        pattern spanned the line break, so "* one\n* two" came out as one long italic.
        """
        for source, tag in (('- one\n- two', 'ul'),
                            ('* one\n* two', 'ul'),
                            ('+ one\n+ two', 'ul'),
                            ('1. one\n2. two', 'ol')):
            with self.subTest(source=source):
                self.campaign.body_markdown = source
                self.campaign.save()
                html = campaigns.render_preview(self.campaign)
                self.assertIn(f'<{tag}', html)
                self.assertEqual(html.count('<li '), 2)
                self.assertNotIn('<em>', html)
                # And the marker itself is gone rather than printed alongside the bullet.
                self.assertNotIn('- one', html)

    def test_emphasis_does_not_span_a_line_break(self):
        self.campaign.body_markdown = 'one *two\nthree* four'
        self.campaign.save()
        html = campaigns.render_preview(self.campaign)
        self.assertNotIn('<em>', html)

    def test_a_paragraph_containing_a_dash_is_not_mistaken_for_a_list(self):
        """All-or-nothing: a chunk where only some lines look like items is prose."""
        self.campaign.body_markdown = 'We open on Friday.\n- and close on Sunday'
        self.campaign.save()
        html = campaigns.render_preview(self.campaign)
        self.assertNotIn('<ul', html)

    def test_text_after_an_image_becomes_a_caption_instead_of_vanishing(self):
        """It used to be dropped on the floor, silently, which is the worst way to lose writing."""
        self.campaign.body_markdown = ('![Opening night](https://x.test/p.jpg)\n'
                                       'Photograph by *Sam Ready*.')
        self.campaign.save()
        html = campaigns.render_preview(self.campaign)
        self.assertIn('https://x.test/p.jpg', html)
        self.assertIn('Photograph by', html)
        self.assertIn('<em>Sam Ready</em>', html)

    def test_a_show_template_gets_its_show_without_the_caller_passing_it(self):
        """The bug this fixes made every show template useless in the app.

        The show came only from `extra_context`, which nothing in the application ever passed —
        so a show campaign rendered with every field blank in the preview, in the test send and
        in the real send alike, and only the tests ever saw one work.
        """
        show = Show.objects.create(
            name='Repetition and Repair', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 4), end=datetime.date(2026, 10, 3),
            description='Fourteen artists on making, unmaking and mending.')
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Repetition and Repair opens',
            template_name='show_opening.mjml')

        # render_preview, deliberately: it is what the page shows and it passes no context.
        html = campaigns.render_preview(campaign)
        self.assertIn('Repetition and Repair', html)
        self.assertIn('September', html)
        self.assertIn('making, unmaking and mending', html)

    def test_the_opening_template_names_the_reception_from_the_events(self):
        """Nobody retypes a reception time — which is the whole reason for the template route."""
        show = Show.objects.create(
            name='Repetition and Repair', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 4), end=datetime.date(2026, 10, 3))
        show.sites.add(self.site)
        Event.objects.create(name='Opening Reception', show=show,
                             date=datetime.date(2026, 9, 4),
                             start=datetime.time(18, 0), end=datetime.time(21, 0))
        Event.objects.create(name='Artist Talk', show=show,
                             date=datetime.date(2026, 9, 19),
                             start=datetime.time(19, 0), end=datetime.time(20, 30))
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Opens Friday',
            template_name='show_opening.mjml')

        html = campaigns.render_preview(campaign)
        # The time comes from the event, which is the whole point — the heading already says
        # "Opening", so the template does not repeat the event's name under it.
        self.assertIn('6–9 PM', html)
        self.assertIn('Friday, 4 September', html)
        # And the later event is listed rather than presented as the opening.
        self.assertIn('Artist Talk', html)

    def test_the_closing_template_leads_with_the_end_date_and_says_it_once(self):
        """Caught by reading the rendered output: an earlier version said the date three times."""
        show = Show.objects.create(
            name='Repetition and Repair', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 4), end=datetime.date(2026, 10, 3))
        show.sites.add(self.site)
        Event.objects.create(name='Opening Reception', show=show,
                             date=datetime.date(2026, 9, 4),
                             start=datetime.time(18, 0), end=datetime.time(21, 0))
        Event.objects.create(name='Closing Party', show=show,
                             date=datetime.date(2026, 10, 3),
                             start=datetime.time(17, 0), end=datetime.time(20, 0))
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Last chance',
            template_name='show_closing.mjml')

        html = campaigns.render_preview(campaign)
        self.assertIn('LAST CHANCE', html)
        # The last day it is open, never the day it comes down: "comes down on Saturday" leaves a
        # reader unsure whether Saturday is already too late.
        self.assertIn('The last day to see Repetition and Repair is Saturday, 3 October', html)
        self.assertNotIn('comes down', html)
        self.assertIn('Last day:', html)
        self.assertIn('Closing Party', html)
        # The closing mailing is not an invitation to the opening.
        self.assertNotIn('Opening Reception', html)
        self.assertEqual(html.count('3 October'), 2,
                         'the closing date belongs in the lead and the date block, not thrice')

    def test_a_closing_event_on_the_last_day_is_not_printed_as_a_second_date(self):
        """The common case: a party on the final day. Repeating the date reads as a discrepancy —
        which is how the wording problem was first noticed, when the two genuinely disagreed."""
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 16))
        show.sites.add(self.site)
        # Two events: `closing` is the last of several, because a show with one reception has an
        # opening rather than a finale.
        Event.objects.create(name='Opening Reception', show=show,
                             date=datetime.date(2026, 7, 25),
                             start=datetime.time(16, 0), end=datetime.time(20, 0))
        Event.objects.create(name='Closing Party', show=show,
                             date=datetime.date(2026, 8, 16),
                             start=datetime.time(17, 0), end=datetime.time(20, 0))
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Last chance',
            template_name='show_closing.mjml')

        html = campaigns.render_preview(campaign)
        self.assertIn('is Sunday, 16 August, ending with Closing Party', html)
        self.assertEqual(html.count('16 August'), 2,
                         'the event is on the last day, so its date must not be printed again')

    def test_a_closing_event_before_the_last_day_carries_its_own_date(self):
        """When they really do differ, say so rather than leaving two bare dates to be reconciled."""
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 16))
        show.sites.add(self.site)
        Event.objects.create(name='Opening Reception', show=show,
                             date=datetime.date(2026, 7, 25),
                             start=datetime.time(16, 0), end=datetime.time(20, 0))
        Event.objects.create(name='Closing Party', show=show,
                             date=datetime.date(2026, 8, 14),
                             start=datetime.time(17, 0), end=datetime.time(20, 0))
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Last chance',
            template_name='show_closing.mjml')

        html = campaigns.render_preview(campaign)
        self.assertIn('is Sunday, 16 August, and Closing Party is on Friday, 14 August', html)

    def test_the_opening_says_up_through_rather_than_until(self):
        """"Until" and "on until" both leave a reader unsure whether the last day is included."""
        show = Show.objects.create(
            name='Full-Feel', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 7, 25), end=datetime.date(2026, 8, 16))
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Opens',
            template_name='show_opening.mjml')

        html = campaigns.render_preview(campaign)
        self.assertIn('Up through 16 August 2026', html)
        self.assertNotIn('Then on until', html)

    def test_a_show_with_one_event_has_an_opening_but_no_closing(self):
        """A single reception is an opening, not a finale, and must not be shown as one."""
        show = Show.objects.create(
            name='Solo', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 4), end=datetime.date(2026, 10, 3))
        show.sites.add(self.site)
        Event.objects.create(name='Opening Reception', show=show,
                             date=datetime.date(2026, 9, 4),
                             start=datetime.time(18, 0), end=datetime.time(21, 0))
        context = campaigns.show_context(show)
        self.assertEqual(context['opening'].name, 'Opening Reception')
        self.assertIsNone(context['closing'])

    def test_a_show_with_no_events_falls_back_to_its_own_dates(self):
        show = Show.objects.create(
            name='Quiet', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 4), end=datetime.date(2026, 10, 3))
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            site=self.site, show=show, subject='Quiet opens',
            template_name='show_opening.mjml')
        html = campaigns.render_preview(campaign)
        self.assertIn('Quiet', html)
        self.assertIn('4 September', html)

    def test_a_show_template_without_a_show_is_a_form_error_not_a_blank_email(self):
        from gallery.forms import CampaignForm
        form = CampaignForm(data={'site': self.site.pk, 'subject': 'Oops',
                                  'template_name': 'show_opening.mjml',
                                  'show': '', 'body_markdown': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('needs one chosen', str(form.errors['show']))

    def test_the_templates_are_offered_in_the_order_a_show_happens(self):
        """Alphabetical put closing before opening, which is not how a show goes."""
        from gallery.forms import CampaignForm
        values = [v for v, _ in CampaignForm().fields['template_name'].choices if v]
        self.assertEqual(values[:3], ['show_announcement.mjml', 'show_opening.mjml',
                                      'show_closing.mjml'])

    def test_the_template_dropdown_shows_readable_names(self):
        from gallery.forms import CampaignForm
        labels = dict(CampaignForm().fields['template_name'].choices)
        self.assertEqual(labels['show_opening.mjml'],
                         'Show opening — dates, reception, a few works')

    def test_a_template_can_place_the_authors_prose_inside_its_layout(self):
        """Templates and Markdown used to be mutually exclusive.

        That meant a designed layout and editable wording were an either/or: staff could write
        something this week, or have it laid out properly, and getting both took a deploy.
        """
        show = Show.objects.create(
            name='Autumn Group Show', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 30))
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            subject='Autumn opens', site=self.site,
            template_name='show_announcement.mjml',
            body_markdown='A note from the curator:\n\n- one thing\n- another')

        html = self._render_with_show(campaign, show)
        # The template's own content...
        self.assertIn('Autumn Group Show', html)
        # ...and the author's prose inside it, as a real list.
        self.assertIn('A note from the curator', html)
        self.assertIn('<ul', html)
        self.assertEqual(html.count('<li '), 2)

    def test_a_template_without_a_body_renders_unchanged(self):
        """The hybrid must be opt-in per campaign, not a blank block on every one."""
        show = Show.objects.create(
            name='Autumn Group Show', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 30))
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            subject='Autumn opens', site=self.site,
            template_name='show_announcement.mjml', body_markdown='   ')
        html = self._render_with_show(campaign, show)
        self.assertIn('Autumn Group Show', html)
        self.assertNotIn('<ul', html)

    def _render_with_show(self, campaign, show):
        """A template campaign needs the objects it names; render_preview cannot know them."""
        stand_in = Subscription(
            pk=0, site=self.site,
            subscriber=Subscriber(pk=0, email='r@example.com'))
        return campaigns.render_campaign(
            campaign, stand_in,
            extra_context={'show': show, 'show_url': 'https://x.test/show/', 'artworks': []})

    def test_author_text_is_escaped_but_generated_markup_is_not(self):
        """The body is markup we generate around text they wrote — only one of those is safe."""
        self.campaign.body_markdown = 'Hello <script>alert(1)</script>'
        self.campaign.save()
        html = campaigns.render_preview(self.campaign)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        # ...and the surrounding MJML still compiled rather than being escaped into text.
        self.assertNotIn('&lt;mj-text', html)

    def test_a_template_campaign_pulls_its_content_from_the_database(self):
        """The main authoring route: a fixed shape, filled from real objects.

        MJML rendered *through* Django's template engine, so the campaign reaches the ORM
        and nobody retypes a date.
        """
        show = Show.objects.create(
            name='Autumn Group Show', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 30),
            description='Fourteen artists on the theme of repetition.')
        show.sites.add(self.site)
        campaign = Campaign.objects.create(
            subject='Autumn Group Show opens', site=self.site,
            template_name='show_announcement.mjml')

        stand_in = Subscription(
            pk=0, site=self.site,
            subscriber=Subscriber(pk=0, email='r@example.com'))
        html = campaigns.render_campaign(
            campaign, stand_in,
            extra_context={'show': show, 'show_url': 'https://x.test/show/',
                           'artworks': []})

        self.assertIn('Autumn Group Show', html)
        self.assertIn('1 September', html)
        self.assertIn('30 September 2026', html)
        self.assertIn('repetition', html)
        # The proof it compiled rather than being escaped into the page as literal markup.
        self.assertGreater(html.count('<table'), 3)
        self.assertNotIn('&lt;mj-', html)

    def test_every_campaign_carries_the_postal_address(self):
        """CAN-SPAM requires it in every marketing email, and it comes from the venue."""
        html = campaigns.render_preview(self.campaign)
        self.assertIn('1207 Tenth Street', html)

    def test_every_campaign_carries_an_unsubscribe_link(self):
        html = campaigns.render_preview(self.campaign)
        self.assertIn('/unsubscribe/', html)

    def test_rendering_a_preview_creates_nothing(self):
        campaigns.render_preview(self.campaign)
        self.assertEqual(Subscriber.objects.count(), 0)

    # --- The send guard ---

    def test_a_campaign_cannot_be_sent_untested(self):
        self.assertFalse(self.campaign.can_send)
        self.assertIn('test', self.campaign.blocked_reason.lower())
        with self.assertRaises(ValueError):
            campaigns.send_campaign(self.campaign)

    def test_editing_after_a_test_rearms_the_guard(self):
        self.campaign.test_sent_at = timezone.now()
        self.campaign.save(update_fields=['test_sent_at'])
        self.assertTrue(self.campaign.can_send)

        self.campaign.subject = 'Changed my mind'
        self.campaign.save()
        self.assertFalse(self.campaign.can_send,
                         'a content change must invalidate the earlier test')
        self.assertIn('changed', self.campaign.blocked_reason.lower())

    def test_a_non_content_change_does_not_re_arm_the_guard(self):
        self.campaign.test_sent_at = timezone.now()
        self.campaign.save(update_fields=['test_sent_at'])
        self.campaign.recipient_count = 5
        self.campaign.save()
        self.assertTrue(self.campaign.can_send)

    # --- Recipients ---

    def test_only_subscribed_people_receive_it(self):
        Subscriber.opt_in(email='yes@example.com', sites=[self.site])
        _, gone = Subscriber.opt_in(email='no@example.com', sites=[self.site])
        gone[0].unsubscribe()
        addresses = {s.subscriber.email for s in campaigns.recipients(self.campaign)}
        self.assertEqual(addresses, {'yes@example.com'})

    def test_a_venues_campaign_does_not_reach_another_venues_list(self):
        elsewhere = Site.objects.create(name='Elsewhere', status=Site.STATUS_PUBLISHED,
                                        country='US')
        Subscriber.opt_in(email='ours@example.com', sites=[self.site])
        Subscriber.opt_in(email='theirs@example.com', sites=[elsewhere])
        addresses = {s.subscriber.email for s in campaigns.recipients(self.campaign)}
        self.assertEqual(addresses, {'ours@example.com'})

    def test_a_network_campaign_reaches_only_the_network_list(self):
        Subscriber.opt_in(email='network@example.com', sites=[None])
        Subscriber.opt_in(email='venue@example.com', sites=[self.site])
        network_campaign = Campaign.objects.create(subject='All of us', site=None)
        addresses = {s.subscriber.email for s in campaigns.recipients(network_campaign)}
        self.assertEqual(addresses, {'network@example.com'})


class UnsubscribeTests(TestCase):
    """The link in the footer, and the button Gmail renders."""

    def setUp(self):
        self.site = Site.objects.create(name='Venue', status=Site.STATUS_PUBLISHED,
                                        country='US')
        self.other = Site.objects.create(name='Other Venue', status=Site.STATUS_PUBLISHED,
                                         country='US')
        self.subscriber, self.subs = Subscriber.opt_in(
            email='reader@example.com', sites=[self.site, self.other])
        self.url = reverse('unsubscribe', kwargs={
            'token': campaigns.unsubscribe_token(self.subs[0])})

    def test_get_asks_rather_than_unsubscribing(self):
        """Security appliances and mail previewers fetch every link in a message."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.subs[0].refresh_from_db()
        self.assertTrue(self.subs[0].is_subscribed, 'a GET must not unsubscribe anyone')

    # RFC 8058: a mail client's one-click POST carries exactly this in the body. The tests
    # below that omit it are standing in for a person pressing the button on our own page,
    # which is now a different response — see the view.
    ONE_CLICK = {'List-Unsubscribe': 'One-Click'}

    def test_post_unsubscribes_one_click(self):
        """What Gmail's Unsubscribe button calls, driven by List-Unsubscribe-Post."""
        response = self.client.post(self.url, self.ONE_CLICK)
        self.assertEqual(response.status_code, 200)
        self.subs[0].refresh_from_db()
        self.assertFalse(self.subs[0].is_subscribed)

    def test_the_one_click_reply_stays_a_bare_200(self):
        """Nobody reads it and the client only wants a 2xx. This test exists because the
        human-facing page was added to the same endpoint: returning it here would send a
        15 KB document to Gmail on every unsubscribe."""
        response = self.client.post(self.url, self.ONE_CLICK)
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertLess(len(response.content), 50)

    def test_a_person_pressing_the_button_gets_a_page_not_a_word(self):
        """It used to answer text/plain "Unsubscribed" — no page, no acknowledgement, not
        even the site around it."""
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response['Content-Type'])
        page = response.content.decode()
        self.assertIn('is unsubscribed from', page)      # the confirmation, first
        self.assertIn('Sorry to see you go', page)
        self.assertIn(reverse('subscribe'), page)        # and a way back
        self.assertIn('site-nav', page)                  # the usual page, not a bare one

    def test_the_page_names_which_list_they_left(self):
        one = self.client.post(self.url).content.decode()
        self.assertIn(self.site.name, one)
        self.assertNotIn('all of our mailing lists', one)

        everything = self.client.post(self.url, {'scope': 'all'}).content.decode()
        self.assertIn('all of our mailing lists', everything)

    def test_a_broken_link_no_longer_publishes_an_email_address(self):
        """The gallery deliberately stopped putting an address on public pages."""
        bad = reverse('unsubscribe', kwargs={'token': 'not-a-real-token'})
        page = self.client.get(bad).content.decode()
        self.assertNotIn('mailto:', page)
        self.assertIn(reverse('contact'), page)

    def test_one_click_leaves_the_other_lists_alone(self):
        self.client.post(self.url, self.ONE_CLICK)
        self.subs[1].refresh_from_db()
        self.assertTrue(self.subs[1].is_subscribed,
                        "a mail client's button must not unsubscribe lists it never named")

    def test_unsubscribe_from_everything_is_one_more_click(self):
        self.client.post(self.url, {'scope': 'all'})
        self.assertFalse(self.subscriber.subscriptions.filter(is_subscribed=True).exists())

    def test_a_tampered_token_still_answers_200(self):
        """Telling a mail client the unsubscribe failed makes it warn the recipient."""
        bad = reverse('unsubscribe', kwargs={'token': 'not-a-real-token'})
        self.assertEqual(self.client.post(bad).status_code, 200)
        self.assertEqual(self.client.get(bad).status_code, 200)

    def test_a_token_is_only_good_for_the_person_it_names(self):
        """The email travels in the token beside the pk, so a reused row cannot be caught
        by an old link."""
        token = campaigns.unsubscribe_token(self.subs[0])
        self.assertIsNotNone(campaigns.subscription_from_token(token))
        # Same row, different person: the token must stop resolving.
        self.subs[0].subscriber.email = 'someone.else@example.com'
        self.subs[0].subscriber.save()
        self.assertIsNone(campaigns.subscription_from_token(token))

    def test_a_token_for_a_deleted_subscription_resolves_to_nothing(self):
        token = campaigns.unsubscribe_token(self.subs[0])
        self.subs[0].delete()
        self.assertIsNone(campaigns.subscription_from_token(token))

    def test_the_message_carries_the_one_click_headers(self):
        campaign = Campaign.objects.create(subject='Hello', site=self.site)
        message = campaigns.build_message(campaign, self.subs[0])
        self.assertIn('List-Unsubscribe', message.extra_headers)
        self.assertEqual(message.extra_headers['List-Unsubscribe-Post'],
                         'List-Unsubscribe=One-Click')


class ArtistMailingListOptInTests(TestCase):
    """The checkbox on the artist profile."""

    def setUp(self):
        self.site = Site.objects.create(name='Default Venue', slug='default-venue',
                                        status=Site.STATUS_PUBLISHED, country='US')
        self.artist = Artist.objects.create(
            name='Opt Artist', first_name='Opt', last_name='Artist',
            email='opt@example.com', phone='', country='US', zipcode='94710')
        self.artist.image.save('opt.jpg', _test_jpg(), save=True)

    def _form(self, subscribe):
        from django.contrib.auth.models import AnonymousUser

        from gallery.forms import ArtistForm
        data = {
            'first_name': 'Opt', 'last_name': 'Artist', 'email': 'opt@example.com',
            'country': 'US', 'zipcode': '94710',
            'subscribe_to_mailing_list': 'on' if subscribe else '',
        }
        return ArtistForm(data=data, instance=self.artist, user=AnonymousUser())

    def test_the_box_is_unticked_by_default(self):
        """Consent is given, not merely not-withdrawn. A pre-ticked box is not consent."""
        from django.contrib.auth.models import AnonymousUser

        from gallery.forms import ArtistForm
        form = ArtistForm(instance=self.artist, user=AnonymousUser())
        self.assertFalse(form.fields['subscribe_to_mailing_list'].initial)

    def test_ticking_it_subscribes_them(self):
        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            form = self._form(subscribe=True)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.assertTrue(Subscription.objects.filter(
            subscriber__email='opt@example.com', site=self.site,
            is_subscribed=True).exists())

    def test_unticking_it_unsubscribes_them(self):
        """Withdrawing consent has to be honoured, not read as "no change"."""
        with self.settings(GALLERY_DEFAULT_SITE_SLUG=self.site.slug):
            Subscriber.opt_in(email='opt@example.com', sites=[self.site])
            form = self._form(subscribe=False)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.assertFalse(Subscription.objects.filter(
            subscriber__email='opt@example.com', is_subscribed=True).exists())


class CsrfFailureTests(TestCase):
    """A rejected POST should tell the person something useful and tell us something
    diagnosable.

    Both halves were missing. Django's page said "CSRF verification failed. Request
    aborted." and its log line said only "Forbidden (CSRF token missing.)", which cannot
    distinguish a body that never arrived from a page served without a token from a token
    that had simply gone stale. An artist was blocked from saving her profile for days and
    neither she nor the logs could say why.
    """

    BOUNDARY = '----testboundary'

    def setUp(self):
        self.user = User.objects.create_user(
            username='csrf@example.com', email='csrf@example.com', password='pw')
        self.artist = Artist.objects.create(
            user=self.user, name='C Surf', first_name='C', last_name='Surf',
            email='csrf@example.com')
        self.url = reverse('gallery:artist_edit', kwargs={'pk': self.artist.pk})

    def _client(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        return client

    def _part(self, name, value):
        return (f'--{self.BOUNDARY}\r\nContent-Disposition: form-data; '
                f'name="{name}"\r\n\r\n{value}\r\n')

    def _post(self, body, headers=None):
        with self.assertLogs('eatart.views.csrf', level='WARNING') as captured:
            response = self._client().generic(
                'POST', self.url, data=body,
                content_type=f'multipart/form-data; boundary={self.BOUNDARY}',
                **(headers or {}))
        return response, '\n'.join(captured.output)

    def test_the_page_says_what_happened_and_what_to_do(self):
        response, _log = self._post(b'')
        self.assertEqual(response.status_code, 403)
        page = response.content.decode()
        self.assertIn('could not be verified', page)
        self.assertIn('nothing was saved', page)
        self.assertNotIn('Request aborted', page)      # Django's unhelpful default

    def _post_meta(self, body, **meta):
        """A POST whose headers we control.

        Needed because RequestFactory.generic only sets CONTENT_TYPE when `data` is
        non-empty (`if data:`), so the natural way to test an empty body drops the very
        header the page branches on — while a real browser sends both.
        """
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        with self.assertLogs('eatart.views.csrf', level='WARNING'):
            return client.generic('POST', self.url, data=body, **meta)

    def test_an_empty_body_with_a_file_attached_names_the_likely_cause(self):
        """The signature of the real fault: Safari wrote the headers, boundary and all,
        and sent nothing. "CSRF verification failed" told the artist nothing she could
        act on; a file her Mac could not read is something she can fix in a minute."""
        page = self._post_meta(
            b'', CONTENT_TYPE=f'multipart/form-data; boundary={self.BOUNDARY}',
            CONTENT_LENGTH='0').content.decode()
        self.assertIn('without any data in it', page)
        self.assertIn('iCloud', page)
        self.assertIn('Download Now', page)

    def test_an_empty_body_with_no_file_does_not_blame_a_file(self):
        """Same symptom, different cause. Telling somebody to re-download a photo they
        never attached would be worse than saying nothing."""
        page = self._post_meta(b'', CONTENT_TYPE='application/x-www-form-urlencoded',
                               CONTENT_LENGTH='0').content.decode()
        self.assertNotIn('iCloud', page)
        self.assertIn('could not be verified', page)

    def test_a_stale_token_still_gets_the_reload_advice(self):
        body = (self._part('csrfmiddlewaretoken', 'x' * 64) + self._part('name', 'X')
                + f'--{self.BOUNDARY}--\r\n')
        page = self._post_meta(
            body.encode(),
            CONTENT_TYPE=f'multipart/form-data; boundary={self.BOUNDARY}').content.decode()
        self.assertNotIn('iCloud', page)
        self.assertIn('Back', page)

    def test_the_log_tells_an_empty_body_from_a_missing_token(self):
        """The distinction that could not be made before, and the one that matters:
        an empty body points upstream, a missing token points at our own page."""
        _r, empty = self._post(b'')
        self.assertIn('empty (arrived with no fields at all)', empty)

        fields = (self._part('name', 'C Surf') + self._part('email', 'csrf@example.com')
                  + f'--{self.BOUNDARY}--\r\n')
        _r, no_token = self._post(fields.encode())
        self.assertIn('but no token', no_token)
        self.assertIn("'name'", no_token)

    def test_a_stale_token_is_reported_as_stale_rather_than_missing(self):
        body = (self._part('csrfmiddlewaretoken', 'x' * 64)
                + self._part('name', 'C Surf') + f'--{self.BOUNDARY}--\r\n')
        _response, log = self._post(body.encode())
        self.assertIn('including the token', log)
        self.assertIn('stale, not absent', log)

    def test_the_log_carries_the_cdn_trace_id(self):
        """A body that arrives empty could be the visitor's machine or our own edge, and
        nothing in our logs could tell them apart. cf-ray identifies the request in
        Cloudflare's Security Events; its absence says Cloudflare was not in the path at
        all, which settles the question just as well."""
        _r, log = self._post(b'', headers={
            'HTTP_CF_RAY': '9a1b2c3d4e5f6789-SJC',
            'HTTP_CF_CONNECTING_IP': '203.0.113.7',
            'HTTP_CF_IPCOUNTRY': 'US'})
        self.assertIn('cf-ray=9a1b2c3d4e5f6789-SJC', log)
        self.assertIn('203.0.113.7', log)

        _r, direct = self._post(b'')
        self.assertIn('no CDN headers', direct)

    def test_the_log_records_field_names_but_never_their_values(self):
        """The form that lands here most often carries a bio, a phone number and a
        postal address. Names are enough to tell the cases apart."""
        body = (self._part('bio', 'SECRETBIOTEXT') + self._part('phone', '5551234567')
                + f'--{self.BOUNDARY}--\r\n')
        _response, log = self._post(body.encode())
        self.assertIn("'bio'", log)
        self.assertIn("'phone'", log)
        self.assertNotIn('SECRETBIOTEXT', log)
        self.assertNotIn('5551234567', log)

    def test_an_unreadable_body_is_named_rather_than_crashing(self):
        """Django's own middleware swallows UnreadablePostError and falls through to
        'token missing', so a broken upload and an absent token look identical in its
        log. Reading the body here is what separates them — and must not raise."""
        from django.http import UnreadablePostError

        from eatart.views.csrf import _body_shape

        class BrokenRequest:
            @property
            def POST(self):
                raise UnreadablePostError('connection broke')

        kind, phrase = _body_shape(BrokenRequest())
        self.assertEqual(kind, 'unreadable')       # what the page branches on
        self.assertIn('connection broke', phrase)  # names the cause, not just the failure

    def test_the_handler_survives_a_request_it_cannot_describe(self):
        """It runs when something is already wrong; raising would turn a 403 into a 500."""
        from eatart.views.csrf import csrf_failure
        from django.test import RequestFactory

        # A bare RequestFactory request has no .user — exactly the shape the handler
        # gets if it ever runs before auth middleware.
        request = RequestFactory().post(self.url)
        response = csrf_failure(request, reason='CSRF token missing.')
        self.assertEqual(response.status_code, 403)


class NoPublicContactDetailsTests(TestCase):
    """The gallery's address and phone number stay off public pages.

    Not a style rule: the point is that every way of reaching the gallery should arrive
    somewhere it gets dealt with — a booking, a mailing-list reply, an enquiry on a work —
    rather than in an inbox nobody watches. Publishing them also hands them to every
    scraper that reads the page.

    Written as a sweep rather than one assertion per template because this has now leaked
    back three separate times: the venue page printed both, the privacy page printed the
    address, and the unsubscribe and claim pages hard-coded it.
    """

    def setUp(self):
        from gallery.models import Site
        self.site = Site.objects.create(
            name='120710', slug='120710', status=Site.STATUS_PUBLISHED,
            street='1207 Tenth Street', city='Berkeley', state='CA',
            postal_code='94710', email='info@120710.art', phone='510-555-0142',
            visits_enabled=True)

    # (url, needs_login). claim-artist is login-required, so asserting against it signed
    # out would test the login page and pass without ever rendering the thing in question.
    def _pages(self):
        return [
            (reverse('privacy'), False),
            (reverse('site_privacy', kwargs={'site_slug': self.site.slug}), False),
            (reverse('contact'), False),
            (self.site.get_absolute_url(), False),
            (reverse('unsubscribe', kwargs={'token': 'not-a-real-token'}), False),
            (reverse('claim_artist'), True),
        ]

    def test_no_public_page_prints_the_gallery_address_or_number(self):
        signed_in = User.objects.create_user(
            username='reader@example.com', email='reader@example.com', password='pw')
        for url, needs_login in self._pages():
            with self.subTest(url=url):
                if needs_login:
                    self.client.force_login(signed_in)
                else:
                    self.client.logout()
                response = self.client.get(url, follow=True)
                self.assertEqual(response.status_code, 200)
                if needs_login:
                    self.assertFalse(response.redirect_chain,
                                     'redirected away — the page was never rendered')
                page = response.content.decode()
                self.assertNotIn('info@120710.art', page)
                self.assertNotIn('510-555-0142', page)
                self.assertNotIn('mailto:', page)
                self.assertNotIn('tel:', page)

    def test_the_venue_page_offers_booking_in_their_place(self):
        """Removing a route is only right if a better one is offered."""
        page = self.client.get(self.site.get_absolute_url()).content.decode()
        self.assertIn(reverse('book_visit'), page)


class ImageColourTests(TestCase):
    """Derived images must keep the colour the photographer saw.

    A photograph of artwork arrives tagged Adobe RGB. Pillow reads the pixels and drops
    the profile, so the derivative carried none — and a browser assumes an untagged image
    is sRGB, a narrower space. The same numbers then mean a duller colour.

    Measured on a cyanotype in a real show, the red channel was wrong by up to 76 levels
    out of 255 while greys were untouched, which is why it reads as "the compression
    ruined it" rather than as a colour-management fault.
    """

    def _tagged(self, colour=(0, 90, 200), mode='RGB', size=(80, 80)):
        """An image whose numbers mean `colour` in Adobe RGB, not in sRGB."""
        from PIL import Image, ImageCms
        adobe = ImageCms.createProfile('sRGB')     # stand-in with a real profile structure
        image = Image.new(mode, size, colour if mode != 'LA' else colour[:2])
        image.info['icc_profile'] = ImageCms.ImageCmsProfile(adobe).tobytes()
        return image

    def test_an_untagged_image_is_left_exactly_alone(self):
        """Untagged already means sRGB by convention; converting would invent data."""
        from PIL import Image, ImageChops

        from gallery.imaging import ToSRGB
        plain = Image.new('RGB', (32, 32), (10, 120, 200))
        self.assertIsNone(
            ImageChops.difference(ToSRGB().process(plain), plain).getbbox())

    def test_a_corrupt_profile_returns_the_image_rather_than_raising(self):
        """A bad profile must not cost the gallery a picture."""
        from PIL import Image

        from gallery.imaging import ToSRGB
        image = Image.new('RGB', (32, 32), (10, 120, 200))
        image.info['icc_profile'] = b'not a real profile'
        with self.assertLogs('gallery.imaging', level='WARNING'):
            self.assertIsNotNone(ToSRGB().process(image))

    def test_transparency_survives_the_conversion(self):
        """LittleCMS returns RGB, so a naive conversion drops alpha and puts a black
        background behind every site logo — the icon specs output PNG for that reason."""
        from gallery.imaging import ToSRGB
        for mode in ('RGBA', 'LA', 'P'):
            with self.subTest(mode=mode):
                image = self._tagged(mode='RGBA')
                image.putpixel((0, 0), (0, 90, 200, 0))
                if mode != 'RGBA':
                    from PIL import Image as PILImage
                    image = (image.convert('P', palette=PILImage.ADAPTIVE)
                             if mode == 'P' else image.convert('LA'))
                    image.info['icc_profile'] = self._tagged().info['icc_profile']
                    if mode == 'P':
                        image.info['transparency'] = 0
                out = ToSRGB().process(image)
                self.assertIn(out.mode, ('RGBA', 'LA'),
                              'alpha was dropped — logos would gain a black background')
                self.assertEqual(out.getpixel((0, 0))[-1], 0)

    def test_a_profile_that_does_not_match_the_pixels_is_ignored_quietly(self):
        """Real production files carry Lab and CMYK profiles on data Pillow reads as RGB.
        LittleCMS answers that with "cannot build transform"; there is no correct
        conversion to attempt, and a bulk regeneration hit it once per spec per image, so
        a traceback each time buried the genuine faults."""
        from PIL import Image, ImageChops, ImageCms

        from gallery.imaging import ToSRGB
        mismatched = Image.new('RGB', (16, 16), (10, 120, 200))
        mismatched.info['icc_profile'] = ImageCms.ImageCmsProfile(
            ImageCms.createProfile('LAB')).tobytes()

        with self.assertLogs('gallery.imaging', level='INFO') as captured:
            out = ToSRGB().process(mismatched)
        joined = '\n'.join(captured.output)
        self.assertIn('does not describe', joined)
        self.assertNotIn('Traceback', joined)
        self.assertNotIn('WARNING', joined)
        self.assertIsNone(ImageChops.difference(out, mismatched).getbbox())

    def test_a_grayscale_image_with_an_rgb_profile_is_left_alone(self):
        from PIL import Image, ImageCms

        from gallery.imaging import ToSRGB
        gray = Image.new('L', (16, 16), 128)
        gray.info['icc_profile'] = ImageCms.ImageCmsProfile(
            ImageCms.createProfile('sRGB')).tobytes()
        with self.assertLogs('gallery.imaging', level='INFO'):
            self.assertEqual(ToSRGB().process(gray).mode, 'L')

    def test_every_spec_on_the_site_runs_the_conversion(self):
        """Twenty-two spec fields across four models, and one left out is one model whose
        pictures are quietly wrong. Asserted over imagekit's registry rather than a list,
        so a spec added later is covered without anyone remembering to add it here."""
        from imagekit.registry import generator_registry

        from gallery.imaging import ToSRGB

        spec_ids = [i for i in generator_registry._generators if i.startswith('gallery:')]
        self.assertGreaterEqual(len(spec_ids), 20,
                                'the registry sweep stopped finding spec fields')
        for spec_id in spec_ids:
            with self.subTest(spec=spec_id):
                processors = generator_registry.get(spec_id, source=None).processors
                self.assertTrue(any(isinstance(p, ToSRGB) for p in processors),
                                f'{spec_id} does not convert to sRGB')


class SiteDirectorTests(TestCase):
    """An admin for one venue and nothing beyond it.

    Most of the surface comes free because it already funnels through can_manage_show —
    events, jurors, reviews, and the pickup/dropoff scheduling all delegate to it. What
    these tests are really for is the other half: that none of it leaks sideways to a
    venue the director does not run.
    """

    def setUp(self):
        from gallery.models import Site
        from gallery.permissions import (can_delete_show, can_manage_artist,
                                         can_manage_artwork, can_manage_show)
        self.can_manage_show = staticmethod(can_manage_show).__func__
        self.can_delete_show = staticmethod(can_delete_show).__func__
        self.can_manage_artist = staticmethod(can_manage_artist).__func__
        self.can_manage_artwork = staticmethod(can_manage_artwork).__func__
        today = datetime.date.today()
        self.mine = Site.objects.create(name='Mine', slug='mine',
                                        status=Site.STATUS_PUBLISHED)
        self.theirs = Site.objects.create(name='Theirs', slug='theirs',
                                          status=Site.STATUS_PUBLISHED)
        self.director = User.objects.create_user(
            username='dir@example.com', email='dir@example.com', password='pw')
        self.mine.directors.add(self.director)

        def show(name, site, status=Show.STATUS_PUBLISHED):
            s = Show.objects.create(name=name, status=status, start=today,
                                    end=today + datetime.timedelta(days=30))
            s.sites.add(site)
            return s
        self.my_show = show('My Show', self.mine)
        self.their_show = show('Their Show', self.theirs)
        self.their_draft = show('Their Draft', self.theirs, Show.STATUS_DRAFT)
        self.my_draft = show('My Draft', self.mine, Show.STATUS_DRAFT)

        def artwork(name, in_show):
            a = Artist.objects.create(name=f'{name} Maker', email=f'{name}@example.com')
            w = Artwork.objects.create(name=name, end_year=2026)
            w.artists.add(a)
            # Submitted, deliberately NOT promoted into show.artworks: that is the state
            # during an open call, and checking only the accepted relation locked a
            # director out of the very work they were jurying.
            ArtworkSubmission.objects.create(show=in_show, artwork=w)
            return a, w
        self.my_artist, self.my_artwork = artwork('Mine', self.my_show)
        self.their_artist, self.their_artwork = artwork('Theirs', self.their_show)

    # --- shows ---

    def test_manages_and_deletes_shows_at_their_venue(self):
        self.assertTrue(self.can_manage_show(self.director, self.my_show))
        self.assertTrue(self.can_delete_show(self.director, self.my_show))

    def test_cannot_touch_another_venues_show(self):
        self.assertFalse(self.can_manage_show(self.director, self.their_show))
        self.assertFalse(self.can_delete_show(self.director, self.their_show))

    def test_sees_their_own_venues_drafts_and_no_others(self):
        from gallery.permissions import visible_show_queryset
        visible = set(visible_show_queryset(Show.objects.all(), self.director)
                      .values_list('name', flat=True))
        self.assertIn('My Draft', visible)
        self.assertNotIn('Their Draft', visible)

    def test_the_show_form_only_offers_their_own_venue(self):
        """Not presentation: a ModelMultipleChoiceField rejects a pk outside its queryset,
        so a posted site id they were never shown fails validation."""
        from gallery.forms import ShowForm
        offered = set(ShowForm(user=self.director).fields['sites'].queryset)
        self.assertEqual(offered, {self.mine})

    # --- artists and artworks, which have no site of their own ---

    def test_manages_artists_and_artworks_shown_at_their_venue(self):
        self.assertTrue(self.can_manage_artist(self.director, self.my_artist))
        self.assertTrue(self.can_manage_artwork(self.director, self.my_artwork))

    def test_cannot_touch_ones_shown_only_elsewhere(self):
        self.assertFalse(self.can_manage_artist(self.director, self.their_artist))
        self.assertFalse(self.can_manage_artwork(self.director, self.their_artwork))

    def test_keeps_hold_of_what_they_just_created(self):
        """Site-ness is derived from shows, so a record not yet in one belongs nowhere.
        Without created_by a director could add somebody on an artist's behalf and be
        locked out of the result immediately."""
        fresh_artist = Artist.objects.create(name='Brand New', email='new@example.com',
                                             created_by=self.director)
        fresh_work = Artwork.objects.create(name='Untitled', end_year=2026,
                                            created_by=self.director)
        self.assertEqual(fresh_artist.artworks.count(), 0)      # in no show at all
        self.assertTrue(self.can_manage_artist(self.director, fresh_artist))
        self.assertTrue(self.can_manage_artwork(self.director, fresh_work))

    # --- the venue itself ---

    def test_edits_their_own_venue_but_cannot_create_or_delete_one(self):
        self.client.force_login(self.director)
        self.assertEqual(
            self.client.get(reverse('gallery:site_edit',
                                    kwargs={'slug': self.mine.slug})).status_code, 200)
        for name, kwargs in (('gallery:site_edit', {'slug': self.theirs.slug}),
                             ('gallery:site_new', {}),
                             ('gallery:site_delete', {'slug': self.mine.slug})):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code,
                                 403)

    def test_cannot_appoint_other_directors(self):
        """Otherwise the role escalates itself: one director could add themselves to every
        other venue, or hand the role to anybody."""
        from gallery.forms import ShowForm  # noqa: F401 — keeps the import block honest
        from gallery.forms import SiteForm
        self.assertNotIn('directors', SiteForm(instance=self.mine, user=self.director).fields)
        staff = User.objects.create_user(username='boss@example.com',
                                         email='boss@example.com', password='pw',
                                         is_staff=True)
        self.assertIn('directors', SiteForm(instance=self.mine, user=staff).fields)

    # --- visits and replies, which are per-venue ---

    def test_sees_only_their_own_venues_bookings_and_replies(self):
        from gallery.models import Visit
        from django.utils import timezone as tz
        when = tz.now() + datetime.timedelta(days=2)
        Visit.objects.create(site=self.mine, when=when, name='My Visitor',
                             email='mine@example.com', party_size=1)
        Visit.objects.create(site=self.theirs, when=when, name='Their Visitor',
                             email='theirs@example.com', party_size=1)
        self.client.force_login(self.director)
        page = self.client.get(reverse('gallery:visit_list')).content.decode()
        self.assertIn('My Visitor', page)
        self.assertNotIn('Their Visitor', page)

    # --- and the things the role deliberately does not include ---

    def test_gets_no_campaign_or_subscriber_access(self):
        """Sending goes out under the gallery's name to its whole list; that stays with
        admins until the gallery says otherwise."""
        self.client.force_login(self.director)
        for name in ('gallery:campaign_list', 'gallery:subscriber_list'):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)


class NudgeInvitedArtistsTests(MediaImageMixin, TestCase):
    """Writing to an invited artist about the one step they have left.

    The reminder this replaces sent everyone the same "please submit your work", whether
    they had never created an account or had a finished profile and simply not pressed the
    last button — and swallowed every delivery failure.
    """

    def setUp(self):
        from gallery.models import ShowInvitation, Site
        self._setup_media()
        today = datetime.date.today()
        self.site = Site.objects.create(name='120710', slug='120710',
                                        status=Site.STATUS_PUBLISHED)
        self.show = Show.objects.create(
            name='after ALBERS', status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_INVITED,
            submission_deadline=today + datetime.timedelta(days=21),
            start=today + datetime.timedelta(days=40),
            end=today + datetime.timedelta(days=70))
        self.show.sites.add(self.site)
        for name, first in (('Mel Ito', 'Mel'), ('Sam Roe', 'Sam')):
            self.show.curators.add(Artist.objects.create(
                name=name, first_name=first, last_name=name.split()[-1],
                email=f'{first.lower()}@example.com'))
        self.url = reverse('gallery:nudge_invited_artists',
                           kwargs={'slug': self.show.slug})
        self.staff = User.objects.create_user(username='boss@example.com',
                                              email='boss@example.com',
                                              password='pw', is_staff=True)
        self.invite = lambda email, **kw: ShowInvitation.objects.create(
            show=self.show, email=email, **kw)

    def tearDown(self):
        self._teardown_media()

    def _complete_artist(self, email, **kw):
        user = User.objects.create_user(username=email, email=email, password='pw')
        return Artist.objects.create(user=user, name=email, first_name='A',
                                     last_name='B', email=email, zipcode='94710',
                                     image=self.TEST_ARTIST_IMAGE, **kw), user

    # --- the ladder ---

    def test_each_artist_is_told_the_step_they_are_actually_stuck_on(self):
        from gallery import nudges
        self.assertEqual(
            nudges.next_step(has_account=False, artist=None, artworks_count=0,
                             submitted_count=0)['key'], nudges.STEP_ACCOUNT)
        self.assertEqual(
            nudges.next_step(has_account=True, artist=None, artworks_count=0,
                             submitted_count=0)['key'], nudges.STEP_PROFILE)
        bare = Artist.objects.create(name='Bare', email='bare@example.com')
        self.assertEqual(
            nudges.next_step(has_account=True, artist=bare, artworks_count=0,
                             submitted_count=0)['key'], nudges.STEP_DETAILS)
        done, _u = self._complete_artist('done@example.com')
        self.assertEqual(
            nudges.next_step(has_account=True, artist=done, artworks_count=0,
                             submitted_count=0)['key'], nudges.STEP_ARTWORK)
        self.assertEqual(
            nudges.next_step(has_account=True, artist=done, artworks_count=2,
                             submitted_count=0)['key'], nudges.STEP_SUBMIT)

    def test_somebody_who_has_submitted_is_never_nudged(self):
        from gallery import nudges
        done, _u = self._complete_artist('sub@example.com')
        self.assertIsNone(nudges.next_step(has_account=True, artist=done,
                                           artworks_count=1, submitted_count=1))

    def test_the_missing_fields_are_named_rather_than_implied(self):
        """"Finish your profile" is the message that made people ask which bit."""
        from gallery import nudges
        partial = Artist.objects.create(name='Part', first_name='Par', last_name='Tial',
                                        email='part@example.com')          # no zip, no photo
        step = nudges.next_step(has_account=True, artist=partial,
                                artworks_count=0, submitted_count=0)
        self.assertIn('zip code', step['short'])
        self.assertIn('photo', step['short'])

    # --- the preview, which sends nothing ---

    def test_get_previews_and_sends_nothing(self):
        self.invite('nobody@example.com')
        self.client.force_login(self.staff)
        mail.outbox.clear()
        page = self.client.get(self.url).content.decode()
        self.assertIn('nobody@example.com', page)
        self.assertIn('No account yet', page)
        self.assertEqual(mail.outbox, [], 'a preview must not send anything')

    def test_the_preview_leaves_out_anyone_who_has_submitted(self):
        from gallery.models import ArtworkSubmission
        artist, user = self._complete_artist('finished@example.com')
        work = Artwork.objects.create(name='Done', end_year=2026)
        work.artists.add(artist)
        ArtworkSubmission.objects.create(show=self.show, artwork=work, submitted_by=user)
        self.invite('finished@example.com')
        self.invite('stalled@example.com')
        self.client.force_login(self.staff)
        page = self.client.get(self.url).content.decode()
        self.assertIn('stalled@example.com', page)
        self.assertNotIn('finished@example.com', page)

    # --- sending ---

    def test_the_email_names_the_curators_the_show_and_the_venue(self):
        self.invite('who@example.com')
        self.client.force_login(self.staff)
        mail.outbox.clear()
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Mel Ito and Sam Roe', html)      # both, not just the first
        self.assertIn('after ALBERS', html)
        self.assertIn('120710', html)
        self.assertIn('/howto/submit-artwork/', html)

    def test_only_somebody_without_an_account_gets_the_accept_link(self):
        """The token binds the invitation to whatever address they sign up with, so it is
        exactly what a person with no account needs — and noise for anyone past that."""
        self.invite('nouser@example.com')
        artist, _u = self._complete_artist('hasuser@example.com')
        self.invite('hasuser@example.com')
        self.client.force_login(self.staff)
        mail.outbox.clear()
        self.client.post(self.url)
        by_to = {m.to[0]: m.alternatives[0][0] for m in mail.outbox}
        self.assertIn('accept-invite', by_to['nouser@example.com'])
        self.assertNotIn('accept-invite', by_to['hasuser@example.com'])

    def test_one_artist_can_be_nudged_alone(self):
        self.invite('a@example.com')
        self.invite('b@example.com')
        self.client.force_login(self.staff)
        mail.outbox.clear()
        self.client.post(self.url, {'email': 'a@example.com'})
        self.assertEqual([m.to[0] for m in mail.outbox], ['a@example.com'])

    def test_sending_records_when_and_a_reply_reaches_the_sender(self):
        invitation = self.invite('rec@example.com')
        self.client.force_login(self.staff)
        mail.outbox.clear()
        self.client.post(self.url)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.nudged_at)
        self.assertEqual(mail.outbox[0].reply_to, ['boss@example.com'])

    def test_a_failure_is_reported_rather_than_swallowed(self):
        """The reminder this replaces passed fail_silently=True, so a nudge that never
        arrived looked exactly like one that did."""
        self.invite('boom@example.com')
        self.client.force_login(self.staff)
        from unittest import mock
        with mock.patch('django.core.mail.EmailMultiAlternatives.send',
                        side_effect=RuntimeError('smtp down')):
            response = self.client.post(self.url, follow=True)
        said = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('Could not send', said)
        self.assertIn('boom@example.com', said)

    # --- the invite table itself ---

    def test_the_table_shows_the_step_the_button_and_the_last_nudge(self):
        """All three were meant to be here and only the button was. It sat fourth among
        four links in the actions cell, and the recorded nudge date was never displayed at
        all — so the page could not answer "who needs chasing, and did I already?"."""
        from django.utils import timezone as tz
        self.invite('fresh@example.com')
        self.invite('already@example.com',
                    nudged_at=tz.now() - datetime.timedelta(days=3))
        self.client.force_login(self.staff)
        page = self.client.get(reverse('gallery:invite_artists',
                                       kwargs={'slug': self.show.slug})).content.decode()
        self.assertIn('Next step', page)                       # the column exists
        self.assertIn('No account yet', page)                  # the step, per row
        self.assertIn('?email=fresh%40example.com', page)       # its own nudge link
        self.assertIn('last ', page)                           # when it last went out

    def test_a_submitted_artist_gets_no_button_and_says_so(self):
        from gallery.models import ArtworkSubmission
        artist, user = self._complete_artist('fin@example.com')
        work = Artwork.objects.create(name='Fin', end_year=2026)
        work.artists.add(artist)
        ArtworkSubmission.objects.create(show=self.show, artwork=work, submitted_by=user)
        self.invite('fin@example.com')
        self.client.force_login(self.staff)
        page = self.client.get(reverse('gallery:invite_artists',
                                       kwargs={'slug': self.show.slug})).content.decode()
        self.assertIn('Submitted — nothing to do', page)
        self.assertNotIn('?email=fin%40example.com', page)

    # --- who may ---

    def test_only_somebody_who_manages_the_show_may_nudge(self):
        self.invite('x@example.com')
        outsider = User.objects.create_user(username='out@example.com',
                                            email='out@example.com', password='pw')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_the_old_blanket_reminder_now_forwards_here(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('gallery:send_submission_reminders',
                                           kwargs={'slug': self.show.slug}))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)


class HealthCheckTests(TestCase):
    """What Railway asks before sending traffic to a new container.

    The deploy hinges on this answering quickly and for the right reason. It must not touch
    the database: a brief database blip would otherwise make Railway conclude a good build
    was broken and roll it back, and a deploy is the worst moment to be wrong about that.
    """

    def test_it_answers_without_touching_the_database(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 0,
                         'a health check that queries conflates two different failures')

    def test_it_is_never_cached(self):
        """A cached 200 would keep saying yes after the container stopped being able to."""
        self.assertIn('no-store', self.client.get('/healthz')['Cache-Control'])

    def test_it_answers_on_the_host_railway_uses(self):
        """Without healthcheck.railway.app in ALLOWED_HOSTS, Django answers 400, the check
        never passes, and the deploy silently never goes live."""
        with self.settings(DEBUG=False, ALLOWED_HOSTS=['healthcheck.railway.app']):
            response = self.client.get('/healthz', HTTP_HOST='healthcheck.railway.app')
        self.assertEqual(response.status_code, 200)

    def test_the_real_allowed_hosts_include_it(self):
        from django.conf import settings
        self.assertIn('healthcheck.railway.app', settings.ALLOWED_HOSTS)

    def test_nothing_but_a_read_is_allowed(self):
        self.assertEqual(self.client.post('/healthz').status_code, 405)

    def test_the_web_process_no_longer_migrates_or_collects(self):
        """Both moved to Railway's pre-deploy phase, where the old container keeps serving
        while they run. Leaving them in the start command is what caused 5-10 minutes of
        downtime per deploy, and made every replica race to migrate."""
        import pathlib
        procfile = (pathlib.Path(__file__).resolve().parent.parent / 'Procfile').read_text()
        self.assertIn('gunicorn', procfile)
        self.assertNotIn('migrate', procfile)
        self.assertNotIn('collectstatic', procfile)


class ArtworkImageSectionsTests(TestCase):
    """The two image sections on the artwork edit form must be findable.

    "Supplemental Images" was an <h5>, which base.css renders at 0.625rem, uppercase, in
    the quiet grey — 10px of muted capitals beside a 1.5rem <legend>. An artist asked the
    gallery to add installation photos for them because they never saw the section existed.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='own@example.com',
                                             email='own@example.com', password='pw')
        artist = Artist.objects.create(user=self.user, name='Own Er', first_name='Own',
                                       last_name='Er', email='own@example.com')
        self.artwork = Artwork.objects.create(name='Piece', end_year=2026,
                                              created_by=self.user)
        self.artwork.artists.add(artist)
        self.client.force_login(self.user)

    def _edit_page(self):
        return self.client.get(reverse('gallery:artwork_edit',
                                       kwargs={'pk': self.artwork.pk})).content.decode()

    def test_both_image_sections_are_headed_like_every_other_section(self):
        """A <legend>, as crispy renders Required and Pricing — not an <h5> at 10px."""
        import re
        page = self._edit_page()
        legends = [re.sub(r'<[^>]+>', '', m) for m in
                   re.findall(r'<legend[^>]*>(.*?)</legend>', page, re.S)]
        joined = ' | '.join(' '.join(t.split()) for t in legends)
        self.assertIn('Layout / 3D image', joined)
        self.assertIn('More images of this work', joined)
        self.assertNotIn('<h5>Supplemental', page)

    def test_the_images_section_says_it_is_optional(self):
        import re
        page = self._edit_page()
        section = re.search(r'<legend[^>]*>More images of this work(.*?)</legend>',
                            page, re.S).group(1)
        self.assertIn('(optional)', section)

    def test_no_rule_is_drawn_between_the_two_sections(self):
        page = self._edit_page()
        between = page.split('Layout / 3D image')[1].split('More images of this work')[0]
        self.assertNotIn('<hr', between)

    def test_the_section_says_what_it_is_for(self):
        """It listed only how to reorder cards, never what to put in them. Installation
        shots are the thing artists were asking the gallery to add for them."""
        self.assertIn('installed', self._edit_page())

    def test_the_create_form_says_these_come_after_saving(self):
        """They only exist on the edit form — an inline formset needs a saved artwork — so
        an artist adding a piece had no reason to know they were possible."""
        page = self.client.get(reverse('gallery:artwork_new')).content.decode()
        self.assertIn('Once this is saved you can add more images', page)


class OnBehalfSubmissionCreditTests(MediaImageMixin, TestCase):
    """A submission counts for the artist whose work it is, not whoever pressed the button.

    "Add artwork on behalf of an artist" records `submitted_by` as the staff member, so
    counting by submitter credited the gallery's own address. The artist's row read zero —
    and because the nudge tool reads the same number, the gallery would have emailed them
    asking them to submit work the gallery had already submitted for them.
    """

    def setUp(self):
        from gallery.models import ShowInvitation, Site
        self._setup_media()
        today = datetime.date.today()
        self.site = Site.objects.create(name='120710', slug='120710',
                                        status=Site.STATUS_PUBLISHED)
        self.show = Show.objects.create(
            name='after ALBERS', status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_INVITED,
            start=today + datetime.timedelta(days=40),
            end=today + datetime.timedelta(days=70))
        self.show.sites.add(self.site)
        self.staff = User.objects.create_user(username='boss@example.com',
                                              email='boss@example.com',
                                              password='pw', is_staff=True)
        self.artist = Artist.objects.create(
            name='Mag Pie', first_name='Mag', last_name='Pie',
            email='mag@example.com', zipcode='94710', image=self.TEST_ARTIST_IMAGE)
        self.invitation = ShowInvitation.objects.create(show=self.show,
                                                        email='mag@example.com')
        self.invite_url = reverse('gallery:invite_artists',
                                  kwargs={'slug': self.show.slug})

    def tearDown(self):
        self._teardown_media()

    def _artwork(self, name):
        work = Artwork.objects.create(name=name, end_year=2026)
        work.artists.add(self.artist)
        return work

    def _add_on_behalf(self, work):
        self.client.force_login(self.staff)
        return self.client.post(
            reverse('gallery:add_artwork_on_behalf', kwargs={'slug': self.show.slug}),
            {'artist': self.artist.pk, 'action': 'add_existing', 'artwork': work.pk},
            follow=True)

    def _counts(self):
        from gallery.views.open_call import submission_counts
        return dict(submission_counts(self.show))

    def test_the_artist_is_credited_not_the_staff_member_who_clicked(self):
        self._add_on_behalf(self._artwork('Piece One'))
        counts = self._counts()
        self.assertEqual(counts.get('mag@example.com'), 1)
        self.assertNotIn('boss@example.com', counts,
                         'the gallery was credited with the artist\'s submission')

    def test_it_shows_on_the_invite_scoreboard(self):
        self._add_on_behalf(self._artwork('Piece One'))
        self._add_on_behalf(self._artwork('Piece Two'))
        import re
        page = self.client.get(self.invite_url).content.decode()
        row = next(tr for tr in re.findall(r'<tr class="border-bottom">.*?</tr>',
                                           page, re.S) if 'mag@example.com' in tr)
        cells = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', c)).strip()
                 for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        self.assertEqual(cells[5], '2')                       # Submitted column

    def test_the_nudge_does_not_chase_them(self):
        """The worst consequence: emailing an artist to submit work already in the show."""
        self._add_on_behalf(self._artwork('Piece One'))
        mail.outbox.clear()
        self.client.post(reverse('gallery:nudge_invited_artists',
                                 kwargs={'slug': self.show.slug}))
        self.assertNotIn('mag@example.com', [m.to[0] for m in mail.outbox])

    def test_their_invitation_survives_an_edit_of_the_email_list(self):
        """The removal guard read the same wrong source, so editing the list would have
        deleted the invitation of somebody whose work was in the show."""
        from gallery.models import ShowInvitation
        self._add_on_behalf(self._artwork('Piece One'))
        self.client.post(self.invite_url, {'action': 'save_emails', 'emails': ''},
                         follow=True)
        self.assertTrue(
            ShowInvitation.objects.filter(show=self.show,
                                          email='mag@example.com').exists())

    def test_a_self_submission_still_counts(self):
        """The regression to avoid: crediting the artist must not stop counting the
        ordinary case, where the artist submitted it themselves."""
        from gallery.models import ArtworkSubmission
        user = User.objects.create_user(username='mag@example.com',
                                        email='mag@example.com', password='pw')
        self.artist.user = user
        self.artist.save(update_fields=['user'])
        ArtworkSubmission.objects.create(show=self.show, artwork=self._artwork('Own'),
                                         submitted_by=user)
        self.assertEqual(self._counts().get('mag@example.com'), 1)

    def test_one_of_each_counts_two_not_three(self):
        """A submission credits a person once, however many ways it could match."""
        from gallery.models import ArtworkSubmission
        user = User.objects.create_user(username='mag@example.com',
                                        email='mag@example.com', password='pw')
        self.artist.user = user
        self.artist.save(update_fields=['user'])
        ArtworkSubmission.objects.create(show=self.show, artwork=self._artwork('Own'),
                                         submitted_by=user)
        self._add_on_behalf(self._artwork('Added for them'))
        self.assertEqual(self._counts().get('mag@example.com'), 2)
