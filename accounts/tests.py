from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import UserNameUpdateForm


class UserNameUpdateFormTests(TestCase):
    def test_requires_first_and_last_name(self):
        form = UserNameUpdateForm(data={
            'first_name': '',
            'last_name': '',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('first_name', form.errors)
        self.assertIn('last_name', form.errors)


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