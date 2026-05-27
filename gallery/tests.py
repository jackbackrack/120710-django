import datetime

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_staff_role
from gallery.models import Artist, Artwork, ArtworkSubmission, Event, Show


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

        self.assertFalse(artist.shows.exists())


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

    def test_search_route_redirects_anonymous_users_to_login(self):
        response = self.client.get('/artist/search/?q=Analytical')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.headers['Location'])

    def test_search_form_is_hidden_for_anonymous_users(self):
        response = self.client.get('/artworks/')

        self.assertNotContains(response, 'placeholder="Search"')

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
class AuthorizationWorkflowTests(TestCase):
    def setUp(self):
        self.artist_user = User.objects.create_user(username='artist@example.com', email='artist@example.com', password='password123')
        self.artist = Artist.objects.create(
            user=self.artist_user,
            name='Ada Lovelace',
            first_name='Ada',
            last_name='Lovelace',
            email='artist@example.com',
            phone='',
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

    def test_curator_can_assign_artists_and_artworks_to_show(self):
        self.client.force_login(self.curator_user)

        response = self.client.post(reverse('gallery:show_edit', kwargs={'pk': self.show.pk}), {
            'name': self.show.name,
            'show_type': self.show.show_type,
            'description': self.show.description or '',
            'start': self.show.start,
            'end': self.show.end,
            'artists': [self.artist.pk],
            'artworks': [self.private_artwork.pk],
            'tags': [],
        })

        self.show.refresh_from_db()

        self.assertRedirects(response, self.show.get_absolute_url())
        self.assertTrue(self.show.artists.filter(pk=self.artist.pk).exists())
        self.assertTrue(self.show.artworks.filter(pk=self.private_artwork.pk).exists())

    def test_artist_can_edit_artwork_without_open_call_available_field(self):
        self.client.force_login(self.artist_user)

        response = self.client.post(reverse('gallery:artwork_edit', kwargs={'pk': self.private_artwork.pk}), {
            'name': self.private_artwork.name,
            'end_year': self.private_artwork.end_year,
            'start_year': '',
            'medium': self.private_artwork.medium or '',
            'width_inches': '10',
            'height_inches': '12',
            'depth_inches': '',
            'price': '',
            'pricing': self.private_artwork.pricing or '',
            'replacement_cost': '',
            'is_sold': '',
            'description': self.private_artwork.description or '',
            'installation': self.private_artwork.installation or '',
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
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class OpenCallFlowTests(TestCase):
    """End-to-end tests for the open call submission, jury review, and promotion flow."""

    def setUp(self):
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
            is_open_call=True,
            submission_deadline=datetime.date.today() + datetime.timedelta(days=7),
        )
        self.show.curators.add(self.curator_artist)

        self.artwork = Artwork.objects.create(
            name='Still Life with Sunflowers',
            created_by=self.artist_user,
            end_year=2026,
        )
        self.artwork.artists.add(self.artist)

    # --- Model property tests ---

    def test_show_is_accepting_submissions_within_deadline(self):
        self.assertTrue(self.show.is_accepting_submissions)

    def test_show_is_not_accepting_submissions_after_deadline(self):
        self.show.submission_deadline = datetime.date.today() - datetime.timedelta(days=1)
        self.show.save(update_fields=['submission_deadline'])
        self.assertFalse(self.show.is_accepting_submissions)

    def test_show_open_call_phase_is_open_before_deadline(self):
        self.assertEqual(self.show.open_call_phase, 'open')

    def test_show_open_call_phase_is_jury_after_deadline(self):
        self.show.submission_deadline = datetime.date.today() - datetime.timedelta(days=1)
        self.show.save(update_fields=['submission_deadline'])
        self.assertEqual(self.show.open_call_phase, 'jury')

    def test_non_open_call_show_has_no_phase(self):
        closed_show = Show.objects.create(
            name='Closed Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
            is_open_call=False,
        )
        self.assertIsNone(closed_show.open_call_phase)

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

    def test_submission_blocked_after_deadline(self):
        self.show.submission_deadline = datetime.date.today() - datetime.timedelta(days=1)
        self.show.save(update_fields=['submission_deadline'])
        self.client.force_login(self.artist_user)

        response = self.client.get(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug})
        )
        # Should redirect away (show is no longer accepting)
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

    def test_curator_can_bulk_update_submission_statuses(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.curator_user)

        response = self.client.post(
            reverse('gallery:show_submissions', kwargs={'slug': self.show.slug}),
            {f'status_{sub.id}': ArtworkSubmission.SELECTED},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, ArtworkSubmission.SELECTED)

    def test_curator_can_reject_submission(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:show_submissions', kwargs={'slug': self.show.slug}),
            {f'status_{sub.id}': ArtworkSubmission.REJECTED},
        )
        sub.refresh_from_db()
        self.assertEqual(sub.status, ArtworkSubmission.REJECTED)

    # --- Promote artworks ---

    def test_curator_can_view_promote_page(self):
        sub = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            status=ArtworkSubmission.SELECTED,
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
            status=ArtworkSubmission.SELECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )

        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())
        self.assertTrue(self.show.artists.filter(pk=self.artist.pk).exists())

    def test_promote_sends_acceptance_email_to_artist(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            status=ArtworkSubmission.SELECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.artist_user.email, mail.outbox[0].recipients())
        self.assertIn(self.show.name, mail.outbox[0].subject)

    def test_promote_sends_rejection_email_for_rejected_submissions(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            status=ArtworkSubmission.REJECTED,
        )
        second_artwork = Artwork.objects.create(
            name='Second Piece', created_by=self.artist_user, end_year=2026
        )
        second_artwork.artists.add(self.artist)
        ArtworkSubmission.objects.create(
            show=self.show, artwork=second_artwork, submitted_by=self.artist_user,
            status=ArtworkSubmission.SELECTED,
        )
        self.client.force_login(self.curator_user)

        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )

        self.assertEqual(len(mail.outbox), 2)

    def test_promote_does_not_add_rejected_artworks_to_show(self):
        ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork, submitted_by=self.artist_user,
            status=ArtworkSubmission.REJECTED,
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

    # --- Date-driven visibility ---

    def test_artwork_not_visible_to_public_before_show_starts(self):
        self.show.start = datetime.date.today() + datetime.timedelta(days=30)
        self.show.save(update_fields=['start'])
        self.artwork.shows.add(self.show)

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertNotContains(response, self.artwork.name)

    def test_artwork_visible_to_public_once_show_has_started(self):
        self.show.start = datetime.date.today() - datetime.timedelta(days=1)
        self.show.save(update_fields=['start'])
        self.artwork.shows.add(self.show)

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertContains(response, self.artwork.name)

    def test_artist_can_view_own_artwork_regardless_of_show_date(self):
        self.show.start = datetime.date.today() + datetime.timedelta(days=30)
        self.show.save(update_fields=['start'])
        self.artwork.shows.add(self.show)
        self.client.force_login(self.artist_user)

        response = self.client.get(self.artwork.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_curator_can_view_all_artworks_regardless_of_show_date(self):
        self.show.start = datetime.date.today() + datetime.timedelta(days=30)
        self.show.save(update_fields=['start'])
        self.artwork.shows.add(self.show)
        self.client.force_login(self.curator_user)

        response = self.client.get(reverse('gallery:artwork_list'))
        self.assertContains(response, self.artwork.name)

    # --- Complete end-to-end flow ---

    def test_full_open_call_flow(self):
        """Submit → select → promote → artwork in show, email sent."""
        # 1. Artist submits artwork
        self.client.force_login(self.artist_user)
        self.client.post(
            reverse('gallery:artwork_submit', kwargs={'slug': self.show.slug}),
            {'artwork': self.artwork.pk, 'statement': 'Statement for the piece.'},
        )
        sub = ArtworkSubmission.objects.get(show=self.show, artwork=self.artwork)
        self.assertEqual(sub.status, ArtworkSubmission.SUBMITTED)

        # 2. Curator selects the submission
        self.client.force_login(self.curator_user)
        self.client.post(
            reverse('gallery:show_submissions', kwargs={'slug': self.show.slug}),
            {f'status_{sub.id}': ArtworkSubmission.SELECTED},
        )
        sub.refresh_from_db()
        self.assertEqual(sub.status, ArtworkSubmission.SELECTED)

        # 3. Curator promotes — adds artwork/artist to show and sends email
        self.client.post(
            reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})
        )

        self.assertTrue(self.show.artworks.filter(pk=self.artwork.pk).exists())
        self.assertTrue(self.show.artists.filter(pk=self.artist.pk).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.artist_user.email, mail.outbox[0].recipients())
