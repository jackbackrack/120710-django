from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_artist_role, add_staff_role
from accounts.signup import apply_google_profile_data, ensure_signup_profile
from gallery.models import Artist, Tag

from accounts.forms import UserNameUpdateForm


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

    def test_signup_page_exposes_local_and_google_options(self):
        response = self.client.get('/accounts/signup/')
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email and password')
        self.assertContains(response, 'Sign up with email and password')
        self.assertContains(response, 'Google account')
        self.assertContains(response, 'Continue with Google')
        self.assertLess(html.index('Google account'), html.index('Email and password'))

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

    def test_ensure_signup_profile_creates_artist_profile_and_role(self):
        user = User.objects.create_user(
            username='ada@example.com',
            email='ada@example.com',
            password='password123',
            first_name='Ada',
            last_name='Lovelace',
        )

        artist = ensure_signup_profile(user)

        self.assertTrue(user.groups.filter(name='artist').exists())
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
class ArtistRoleUpdateViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(username='staff@example.com', email='staff@example.com', password='password123')
        add_staff_role(self.staff_user)

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
        self.tag = Tag.objects.create(name='Installation')

    def test_staff_can_promote_artist_to_curator_and_assign_tags(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(reverse('artist_role_edit', kwargs={'pk': self.artist.pk}), {
            'is_curator': 'on',
            'curator_tags': [self.tag.pk],
        })

        self.artist_user.refresh_from_db()

        self.assertRedirects(response, self.artist.get_absolute_url())
        self.assertTrue(self.artist_user.groups.filter(name='curator').exists())
        self.assertQuerySetEqual(self.artist_user.curator_tags.all(), [self.tag], transform=lambda value: value)