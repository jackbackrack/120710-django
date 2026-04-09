import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_artist_role, add_curator_role, add_staff_role
from gallery.models import Artist, Artwork, Event, Show
from gallery.models.tags import ensure_open_call_tag


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
        )
        self.show.curators.add(self.artist)
        self.artwork = Artwork.objects.create(
            name='Analytical Engine Study',
            end_year=1843,
            medium='Ink on paper',
            is_public=True,
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
            '/artist/search/?q=Analytical',
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

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
class AuthorizationWorkflowTests(TestCase):
    def setUp(self):
        self.artist_user = User.objects.create_user(username='artist@example.com', email='artist@example.com', password='password123')
        add_artist_role(self.artist_user)
        self.artist = Artist.objects.create(
            user=self.artist_user,
            name='Ada Lovelace',
            first_name='Ada',
            last_name='Lovelace',
            email='artist@example.com',
            phone='',
        )

        self.curator_user = User.objects.create_user(username='curator@example.com', email='curator@example.com', password='password123')
        add_curator_role(self.curator_user)
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
            managing_curator=self.curator_user,
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        self.show.curators.add(self.curator_artist)

        self.event = Event.objects.create(
            name='Opening Reception',
            show=self.show,
            managing_curator=self.curator_user,
            date=datetime.date.today(),
            start=datetime.time(18, 0),
            end=datetime.time(20, 0),
        )

        self.private_artwork = Artwork.objects.create(
            name='Private Study',
            created_by=self.artist_user,
            end_year=2024,
            is_public=False,
        )
        self.private_artwork.artists.add(self.artist)

        self.public_artwork = Artwork.objects.create(
            name='Public Study',
            created_by=self.artist_user,
            end_year=2024,
            is_public=True,
        )
        self.public_artwork.artists.add(self.artist)
        self.public_artwork.shows.add(self.show)

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

    def test_curator_can_browse_private_artworks(self):
        self.client.force_login(self.curator_user)

        response = self.client.get(reverse('gallery:artwork_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Private Study')

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

    def test_curator_can_assign_artists_and_artworks_to_show_and_publish_artwork(self):
        self.client.force_login(self.curator_user)

        response = self.client.post(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}), {
            'name': self.show.name,
            'description': self.show.description or '',
            'start': self.show.start,
            'end': self.show.end,
            'artists': [self.artist.pk],
            'artworks': [self.private_artwork.pk],
            'tags': [],
        })

        self.show.refresh_from_db()
        self.private_artwork.refresh_from_db()

        self.assertRedirects(response, self.show.get_absolute_url())
        self.assertTrue(self.show.artists.filter(pk=self.artist.pk).exists())
        self.assertTrue(self.show.artworks.filter(pk=self.private_artwork.pk).exists())
        self.assertTrue(self.private_artwork.is_public)

    def test_open_call_dashboard_is_curator_only_and_lists_opted_in_work(self):
        self.show.is_open_call = True
        self.show.save(update_fields=['is_open_call'])
        self.private_artwork.open_call_available = True
        self.private_artwork.save(update_fields=['open_call_available'])

        anonymous_response = self.client.get(reverse('gallery:open_call_dashboard'))

        self.client.force_login(self.artist_user)
        artist_response = self.client.get(reverse('gallery:open_call_dashboard'))

        self.client.force_login(self.curator_user)
        curator_response = self.client.get(reverse('gallery:open_call_dashboard'))

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertEqual(artist_response.status_code, 403)
        self.assertEqual(curator_response.status_code, 200)
        self.assertContains(curator_response, self.private_artwork.name)
        self.assertContains(curator_response, self.show.name)

    def test_artist_open_call_page_is_artist_facing_and_navigation_is_role_aware(self):
        self.show.is_open_call = True
        self.show.save(update_fields=['is_open_call'])
        self.private_artwork.open_call_available = True
        self.private_artwork.save(update_fields=['open_call_available'])

        anonymous_response = self.client.get(reverse('gallery:artist_open_call'))

        self.client.force_login(self.artist_user)
        artist_page_response = self.client.get(reverse('gallery:artist_open_call'))
        artist_nav_response = self.client.get(reverse('gallery:artwork_list'))

        self.client.force_login(self.curator_user)
        curator_nav_response = self.client.get(reverse('gallery:artwork_list'))

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertEqual(artist_page_response.status_code, 200)
        self.assertContains(artist_page_response, self.private_artwork.name)
        self.assertContains(artist_page_response, self.show.name)
        self.assertContains(artist_nav_response, 'My Open Call')
        self.assertNotContains(artist_nav_response, 'Open Call Dashboard')
        self.assertContains(curator_nav_response, 'My Open Call')
        self.assertContains(curator_nav_response, 'Open Call Dashboard')

    def test_artist_can_mark_artwork_available_for_open_call(self):
        self.client.force_login(self.artist_user)

        response = self.client.post(reverse('gallery:artwork_edit', kwargs={'pk': self.private_artwork.pk}), {
            'name': self.private_artwork.name,
            'end_year': self.private_artwork.end_year,
            'start_year': '',
            'medium': self.private_artwork.medium or '',
            'dimensions': self.private_artwork.dimensions or '',
            'price': '',
            'pricing': self.private_artwork.pricing or '',
            'replacement_cost': '',
            'is_sold': '',
            'open_call_available': 'on',
            'description': self.private_artwork.description or '',
            'installation': self.private_artwork.installation or '',
        })

        self.private_artwork.refresh_from_db()

        self.assertRedirects(response, self.private_artwork.get_absolute_url())
        self.assertTrue(self.private_artwork.open_call_available)
        self.assertTrue(self.private_artwork.tags.filter(slug='open-call').exists())

    def test_open_call_show_is_tagged_and_limited_to_opted_in_artworks(self):
        ensure_open_call_tag()
        self.private_artwork.open_call_available = True
        self.private_artwork.save(update_fields=['open_call_available'])

        self.client.force_login(self.curator_user)
        response = self.client.post(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}), {
            'name': self.show.name,
            'description': self.show.description or '',
            'is_open_call': 'on',
            'start': self.show.start,
            'end': self.show.end,
            'artists': [self.artist.pk],
            'artworks': [self.private_artwork.pk],
            'tags': [],
        })

        self.show.refresh_from_db()

        self.assertRedirects(response, self.show.get_absolute_url())
        self.assertTrue(self.show.is_open_call)
        self.assertTrue(self.show.tags.filter(slug='open-call').exists())

        other_artwork = Artwork.objects.create(
            name='Not Eligible',
            created_by=self.artist_user,
            end_year=2024,
            is_public=False,
            open_call_available=False,
        )
        other_artwork.artists.add(self.artist)
        form_response = self.client.get(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}))

        self.assertContains(form_response, 'Private Study')
        self.assertNotContains(form_response, 'Not Eligible')

    def test_authenticated_show_surfaces_expose_slug_subpage_links(self):
        self.client.force_login(self.staff_user)

        show_detail_response = self.client.get(self.show.get_absolute_url())
        show_list_response = self.client.get(reverse('gallery:show_list'))
        artwork_detail_response = self.client.get(self.public_artwork.get_absolute_url())
        homepage_response = self.client.get(reverse('index'))

        for response in (show_detail_response, show_list_response, artwork_detail_response, homepage_response):
            self.assertContains(response, self.show.get_placards_url())
            self.assertContains(response, self.show.get_instagram_url())
