import datetime
import io
import os
import shutil
import tempfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_staff_role
from gallery.models import Artist, Artwork, ArtworkSubmission, Event, Show, ShowArtworkNumber, Site


def _make_test_image_dir():
    """Return a temp directory containing artist_images/test.jpg (1x1 JPEG)."""
    from PIL import Image as PILImage
    tmp = tempfile.mkdtemp()
    img_dir = os.path.join(tmp, 'artist_images')
    os.makedirs(img_dir)
    PILImage.new('RGB', (2, 2), 'white').save(os.path.join(img_dir, 'test.jpg'), 'JPEG')
    return tmp


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
        # Artist missing photo and zipcode → redirect to edit page with ?highlight=
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
        response = self.client.get(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers['Location']
        self.assertIn('artist', location)
        self.assertIn('highlight=', location)
        self.assertIn('image', location)
        self.assertIn('zipcode', location)
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


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
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
            'country': 'USA',
            'email': '',
            'phone': '',
            'instagram': '',
            'website': '',
            'description': '',
            'status': Site.STATUS_DRAFT,
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
        })
        self.assertTrue(Site.objects.filter(name='New Test Site').exists())
        new_site = Site.objects.get(name='New Test Site')
        self.assertRedirects(response, new_site.get_absolute_url())

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
        response = self.client.get(reverse('gallery:site_artist_list', kwargs={'slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.artist, response.context['artists'])

    def test_site_artist_list_excludes_artist_who_did_not_show_there(self):
        response = self.client.get(reverse('gallery:site_artist_list', kwargs={'slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.other_artist, response.context['artists'])

    # ── Site artwork list ─────────────────────────────────────────────────────

    def test_site_artwork_list_includes_artwork_shown_there(self):
        response = self.client.get(reverse('gallery:site_artwork_list', kwargs={'slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.artwork, response.context['artworks'])

    def test_site_artwork_list_excludes_artwork_not_shown_there(self):
        response = self.client.get(reverse('gallery:site_artwork_list', kwargs={'slug': self.published_site.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.other_artwork, response.context['artworks'])


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
