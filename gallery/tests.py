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
from gallery.models import LinkTreeEntry
from gallery import calendars
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
    """A tiny real JPEG upload, for the places a profile photo is now required."""
    import io
    from PIL import Image as _P
    b = io.BytesIO(); _P.new('RGB', (240, 240), (120, 140, 110)).save(b, 'JPEG')
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

    def test_signed_in_visitor_is_led_through_every_remaining_step(self):
        user = User.objects.create_user(
            username='led@example.com', email='led@example.com', password='pw')
        self.client.force_login(user)

        # No profile at all
        self.assertIn('Set up your artist profile', self._cta()[0])

        artist = Artist.objects.create(user=user, first_name='Led', last_name='Through',
                                       email='led@example.com')
        self.assertIn('Finish your profile', self._cta()[0])

        artist.zipcode = '94710'
        artist.save()
        self.assertIn('Finish your profile', self._cta()[0])   # photo outstanding

        artist.image = _test_jpg('led.jpg')
        artist.save()
        self.assertEqual(self._cta()[0], 'Submit Artwork')

        art = Artwork.objects.create(name='Piece', end_year=2025)
        art.artists.add(artist)
        ArtworkSubmission.objects.create(show=self.show, artwork=art, submitted_by=user)
        self.assertEqual(self._cta()[0], 'Submit another work')

    def test_cta_tracks_profile_completeness_then_offers_submit(self):
        user = User.objects.create_user(
            username='cta@example.com', email='cta@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='Cee', last_name='Tee',
                                       email='cta@example.com')
        self.client.force_login(user)
        label, url = self._cta()
        self.assertIn('Finish your profile', label)
        self.assertIn('next=', url)

        artist.zipcode = '94710'
        artist.save()
        label, _url = self._cta()
        self.assertIn('Finish your profile', label)   # photo still outstanding

        artist.image = _test_jpg('cta.jpg')
        artist.save()
        label, _url = self._cta()
        self.assertEqual(label, 'Submit Artwork')

    def test_submitting_requires_a_photo_but_says_so_before_you_start(self):
        user = User.objects.create_user(
            username='nop@example.com', email='nop@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='No', last_name='Photo',
                                       email='nop@example.com', zipcode='94710')
        self.client.force_login(user)
        # The show page names the requirement, so nobody meets it as a surprise...
        label, _url = self._cta()
        self.assertIn('Finish your profile', label)
        # ...and the submit page itself still refuses, on GET, before any form is
        # filled in, carrying a way back.
        r = self.client.get(self.submit_url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('image', r.headers['Location'])
        self.assertIn('next=', r.headers['Location'])

        artist.image = _test_jpg('ok.jpg')
        artist.save()
        self.assertEqual(self.client.get(self.submit_url).status_code, 200)

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

    def test_google_signup_arrives_with_its_avatar(self):
        """Google hands us a picture at signup, so a Google artist satisfies the
        photo requirement without ever seeing the field."""
        from unittest import mock
        from accounts.signup import import_google_avatar
        artist = Artist.objects.create(first_name='Gina', last_name='Google',
                                       email='gg@example.com', zipcode='94710')
        jpg = _test_jpg('g.jpg').read()

        class _Resp:
            content = jpg
            headers = {'Content-Type': 'image/jpeg'}
            def raise_for_status(self):
                pass

        with mock.patch('requests.get', return_value=_Resp()) as get:
            self.assertTrue(import_google_avatar(
                artist, {'picture': 'https://lh3.googleusercontent.com/a/x=s96-c'}))
        # Asks Google for something big enough to print, not the 96px default.
        self.assertIn('=s600-c', get.call_args[0][0])
        artist.refresh_from_db()
        self.assertTrue(artist.image)

    def test_google_avatar_failure_never_breaks_signup(self):
        from unittest import mock
        from accounts.signup import import_google_avatar
        artist = Artist.objects.create(first_name='Gus', last_name='Glitch',
                                       email='gl@example.com')
        with mock.patch('requests.get', side_effect=OSError('network down')):
            self.assertFalse(import_google_avatar(artist, {'picture': 'https://x/y=s96-c'}))
        artist.refresh_from_db()
        self.assertFalse(artist.image)

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
        self.assertIn('Finish your profile', label)
        self.assertIn('next=', url)

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
        self.assertEqual(self._cta(self.client)[0], 'Submit Artwork')

    def test_signed_in_invitee_completes_the_profile_and_submits(self):
        user = User.objects.create_user(
            username='in@example.com', email='in@example.com', password='pw')
        artist = Artist.objects.create(user=user, first_name='In', last_name='Vitee',
                                       email='in@example.com')   # no zip, no photo
        inv = ShowInvitation.objects.create(show=self.show, email='in@example.com')
        self.client.force_login(user)
        self.client.get(inv.get_accept_url(), follow=True)

        self.assertIn('Finish your profile', self._cta(self.client)[0])
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
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image as PILImage

        buf = io.BytesIO()
        PILImage.new('RGB', (40, 40), (120, 140, 130)).save(buf, 'JPEG')
        self.client.post(reverse('gallery:artist_new'), {
            'first_name': 'Wren', 'last_name': 'Halloway',
            'email': 'caregiver@example.com', 'country': 'US', 'zipcode': '94710', 'street': '1 Test St', 'city': 'Berkeley', 'state': 'CA',
            'bio': '', 'statement': '', 'phone': '', 'website': '',
            'instagram': '', 'venmo': '',
            'image': SimpleUploadedFile('w.jpg', buf.getvalue(), 'image/jpeg'),
        })
        created = Artist.objects.filter(first_name='Wren').first()
        self.assertIsNotNone(created, 'the curator could not create the artist at all')
        self.assertIsNone(
            created.user,
            'a profile a curator creates for another artist must not be linked to the '
            'curator — that is what makes it claimable later')

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
        self.assertIn('Finish your profile', self.client.get('/').content.decode())

        artist.zipcode = '94710'
        artist.image = _test_jpg('home.jpg')
        artist.save()
        body = self.client.get('/').content.decode()
        self.assertIn('Submit Artwork', body)
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
        """It used to be hidden, leaving shows listed that could not be acted on."""
        body = self.client.get(self.artist.get_absolute_url(), follow=True).content.decode()
        self.assertIn('Shows Accepting Submissions', body)
        self.assertIn(reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug}),
                      body)

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
        response = self._page('contact', self.gallery)
        self.assertContains(response, 'hello@first.example')
        self.assertContains(response, '555-0100')

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
        self.show = Show.objects.create(
            name='Summer Show', status=Show.STATUS_PUBLISHED,
            start=datetime.date(2026, 8, 1), end=datetime.date(2026, 8, 31))
        self.show.sites.add(self.site)
        self.event = Event.objects.create(
            show=self.show, name='Opening Reception', date=datetime.date(2026, 8, 2),
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
        """18:00 in Berkeley in August is PDT, so 01:00Z the following day."""
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
        body = self._ics()
        self.assertIn('DTSTART:20260803T010000Z', body)   # August, PDT (-7)
        self.assertIn('DTSTART:20270112T020000Z', body)   # January, PST (-8)

    # --- The exclusive DTEND ---

    def test_all_day_show_end_is_exclusive(self):
        """A show ending 31 Aug must publish DTEND 1 Sep, or clients draw it a day short."""
        body = self._ics()
        self.assertIn('DTSTART;VALUE=DATE:20260801', body)
        self.assertIn('DTEND;VALUE=DATE:20260901', body)

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

    def test_draft_venue_calendar_is_not_public(self):
        draft = Site.objects.create(name='Draft Venue', status=Site.STATUS_DRAFT,
                                    state='CA', country='US')
        for name in ('site_calendar', 'site_shows_ics'):
            response = self.client.get(
                reverse(f'gallery:{name}', kwargs={'site_slug': draft.slug}))
            self.assertEqual(response.status_code, 404, name)
