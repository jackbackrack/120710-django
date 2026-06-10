from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_staff_role
from accounts.signup import apply_google_profile_data, ensure_signup_profile
from gallery.models import Artist, Show

from accounts.forms import UserNameUpdateForm


STORAGE_OVERRIDE = override_settings(
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
class UserNameUpdateFormTests(TestCase):
    def test_requires_first_and_last_name(self):
        form = UserNameUpdateForm(data={
            'first_name': '',
            'last_name': '',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('first_name', form.errors)
        self.assertIn('last_name', form.errors)


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
class UserNameUpdateViewTests(TestCase):
    def test_profile_update_persists_names(self):
        user = User.objects.create_user(username='artist@example.com', password='password123')
        self.client.force_login(user)

        response = self.client.post(reverse('account_profile'), {
            'first_name': 'Ada',
            'last_name': 'Lovelace',
        })

        user.refresh_from_db()

        self.assertRedirects(response, reverse('account_profile'))
        self.assertEqual(user.first_name, 'Ada')
        self.assertEqual(user.last_name, 'Lovelace')


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
class SignupFlowTests(TestCase):
    def test_signup_navigation_link_is_hidden_but_signup_page_is_public(self):
        home_response = self.client.get(reverse('index'))
        signup_response = self.client.get('/accounts/signup/')

        self.assertEqual(home_response.status_code, 200)
        self.assertNotContains(home_response, 'href="/accounts/signup/"', html=True)
        self.assertEqual(signup_response.status_code, 200)

    import unittest
    @unittest.skip("Skip: requires SocialApp and provider login URL")
    def test_signup_page_exposes_local_and_google_options(self):
        response = self.client.get('/accounts/signup/')
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email and password')
        self.assertContains(response, 'Sign up with email and password')
        self.assertContains(response, 'Google account')
        self.assertContains(response, 'Continue with Google')
        self.assertLess(html.index('Google account'), html.index('Email and password'))

    @unittest.skip("Skip: requires SocialApp and provider login URL")
    def test_login_page_exposes_local_and_google_options(self):
        response = self.client.get('/accounts/login/')
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email and password')
        self.assertContains(response, 'Log in with email and password')
        self.assertContains(response, 'Google account')
        self.assertContains(response, 'Continue with Google')
        self.assertLess(html.index('Google account'), html.index('Email and password'))

    def test_apply_google_profile_data_prefills_user_fields(self):
        user = User(username='').__class__()
        changed_fields = apply_google_profile_data(user, {
            'email': 'ada@example.com',
            'given_name': 'Ada',
            'family_name': 'Lovelace',
        })

        self.assertCountEqual(changed_fields, ['first_name', 'last_name', 'email', 'username'])
        self.assertEqual(user.first_name, 'Ada')
        self.assertEqual(user.last_name, 'Lovelace')
        self.assertEqual(user.email, 'ada@example.com')
        self.assertEqual(user.username, 'ada@example.com')

    def test_ensure_signup_profile_creates_artist_profile(self):
        user = User.objects.create_user(
            username='ada@example.com',
            email='ada@example.com',
            password='password123',
            first_name='Ada',
            last_name='Lovelace',
        )

        artist, _ = ensure_signup_profile(user)

        self.assertEqual(artist.user, user)
        self.assertEqual(artist.name, 'Ada Lovelace')
        self.assertEqual(artist.email, 'ada@example.com')


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
class CuratorVisibilityTests(TestCase):
    def setUp(self):
        self.artist_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='password123'
        )
        self.artist = Artist.objects.create(
            user=self.artist_user,
            name='Ada Lovelace',
            first_name='Ada',
            last_name='Lovelace',
            email='artist@example.com',
            phone='',
        )

    def test_artist_profile_is_visible_when_assigned_as_curator(self):
        import datetime
        from django.contrib.auth.models import AnonymousUser
        from gallery.permissions import visible_artist_queryset

        show = Show.objects.create(
            name='Test Show',
            start=datetime.date.today(),
            end=datetime.date.today(),
        )
        show.curators.add(self.artist)

        qs = Artist.objects.filter(visible_artist_queryset(AnonymousUser()))
        self.assertIn(self.artist, qs)


def _make_unlinked_artist(email='old@example.com', name='Old Artist'):
    return Artist.objects.create(
        user=None,
        name=name,
        first_name=name.split()[0],
        last_name=name.split()[-1],
        email=email,
        phone='',
    )


@STORAGE_OVERRIDE
class ClaimArtistViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='new@example.com', email='new@example.com', password='password123'
        )

    def test_redirects_anonymous_to_login(self):
        response = self.client.get('/accounts/claim-artist/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_claim_matches_exact_email(self):
        artist = _make_unlinked_artist(email='new@example.com')
        self.client.force_login(self.user)

        response = self.client.post('/accounts/claim-artist/', {'email': 'new@example.com'})
        artist.refresh_from_db()

        self.assertEqual(artist.user, self.user)
        self.assertRedirects(response, reverse('gallery:artist_edit', kwargs={'pk': artist.pk}))

    def test_claim_matches_case_insensitive_email(self):
        artist = _make_unlinked_artist(email='Old@Example.COM')
        self.user.email = 'new@example.com'
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.post('/accounts/claim-artist/', {'email': 'OLD@EXAMPLE.com'})
        artist.refresh_from_db()

        self.assertEqual(artist.user, self.user)
        self.assertRedirects(response, reverse('gallery:artist_edit', kwargs={'pk': artist.pk}))

    def test_claim_no_match_returns_form_error(self):
        self.client.force_login(self.user)

        response = self.client.post('/accounts/claim-artist/', {'email': 'nobody@example.com'})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'email', 'No unlinked artist record was found with that email address.')

    def test_claim_does_not_link_already_linked_artist(self):
        other_user = User.objects.create_user(username='other@example.com', password='pw')
        Artist.objects.create(user=other_user, name='Taken Artist', email='taken@example.com', phone='')
        self.client.force_login(self.user)

        response = self.client.post('/accounts/claim-artist/', {'email': 'taken@example.com'})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'email', 'No unlinked artist record was found with that email address.')

    def test_already_linked_user_is_redirected(self):
        artist = Artist.objects.create(
            user=self.user, name='My Artist', email='new@example.com', phone='',
            bio='Some bio text',
        )
        self.client.force_login(self.user)

        response = self.client.get('/accounts/claim-artist/')

        self.assertRedirects(response, artist.get_absolute_url())

    def test_empty_linked_profile_allows_claim(self):
        _existing = Artist.objects.create(
            user=self.user, name='New User', email='new@example.com', phone=''
        )
        old_artist = _make_unlinked_artist(email='old@example.com', name='Old Artist')
        self.client.force_login(self.user)

        response = self.client.post('/accounts/claim-artist/', {'email': 'old@example.com'})
        old_artist.refresh_from_db()

        self.assertEqual(old_artist.user, self.user)
        self.assertRedirects(response, reverse('gallery:artist_edit', kwargs={'pk': old_artist.pk}))


@STORAGE_OVERRIDE
class LinkArtistToUserViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff@example.com', email='staff@example.com', password='password123'
        )
        add_staff_role(self.staff_user)

        self.target_user = User.objects.create_user(
            username='artist@example.com', email='artist@example.com', password='password123'
        )
        self.artist = _make_unlinked_artist(email='artist@example.com', name='Unlinked Artist')

    def test_non_staff_gets_403(self):
        regular = User.objects.create_user(username='regular@example.com', password='pw')
        self.client.force_login(regular)

        response = self.client.get('/accounts/link-artists/')

        self.assertEqual(response.status_code, 403)

    def test_staff_can_link_artist_to_user(self):
        self.client.force_login(self.staff_user)

        response = self.client.post('/accounts/link-artists/', {
            'artist': self.artist.pk,
            'user': self.target_user.pk,
        })

        self.artist.refresh_from_db()
        self.assertEqual(self.artist.user, self.target_user)
        self.assertRedirects(response, '/accounts/link-artists/')

    def test_staff_link_page_only_shows_unlinked_artists(self):
        linked_artist = Artist.objects.create(
            user=self.target_user, name='Linked', email='linked@example.com', phone=''
        )
        self.client.force_login(self.staff_user)

        response = self.client.get('/accounts/link-artists/')

        artist_field = response.context['form'].fields['artist']
        ids_in_dropdown = list(artist_field.queryset.values_list('pk', flat=True))
        self.assertIn(self.artist.pk, ids_in_dropdown)
        self.assertNotIn(linked_artist.pk, ids_in_dropdown)


@STORAGE_OVERRIDE
class EnsureSignupProfileEmailCaseTests(TestCase):
    def test_case_insensitive_email_links_existing_artist(self):
        Artist.objects.create(
            user=None,
            name='Ada Lovelace',
            first_name='Ada',
            last_name='Lovelace',
            email='Ada@Example.COM',
            phone='',
        )
        user = User.objects.create_user(
            username='ada@example.com',
            email='ada@example.com',
            password='password123',
            first_name='Ada',
            last_name='Lovelace',
        )

        artist, _ = ensure_signup_profile(user)

        self.assertEqual(artist.user, user)
        self.assertEqual(artist.email, 'Ada@Example.COM')
