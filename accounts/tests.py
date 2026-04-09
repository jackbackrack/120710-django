from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_artist_role, add_staff_role
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