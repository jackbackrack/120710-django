import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.roles import add_artist_role, add_curator_role, add_juror_role
from gallery.models import Artist, Artwork, Show
from reviews.models import ArtworkReview, ShowJuror


class ReviewPermissionsAndWorkflowTests(TestCase):
    def setUp(self):
        self.manager_user = User.objects.create_user(
            username='manager@example.com',
            email='manager@example.com',
            password='password123',
        )
        add_curator_role(self.manager_user)

        self.other_curator_user = User.objects.create_user(
            username='other-curator@example.com',
            email='other-curator@example.com',
            password='password123',
        )
        add_curator_role(self.other_curator_user)

        self.artist_user = User.objects.create_user(
            username='artist@example.com',
            email='artist@example.com',
            password='password123',
        )
        add_artist_role(self.artist_user)

        self.juror_user = User.objects.create_user(
            username='juror@example.com',
            email='juror@example.com',
            password='password123',
        )

        self.unassigned_juror_user = User.objects.create_user(
            username='juror-unassigned@example.com',
            email='juror-unassigned@example.com',
            password='password123',
        )
        add_juror_role(self.unassigned_juror_user)

        self.artist_profile = Artist.objects.create(
            user=self.artist_user,
            name='Artist One',
            first_name='Artist',
            last_name='One',
            email='artist@example.com',
            phone='555-1000',
        )

        self.show = Show.objects.create(
            name='Juried Spring Show',
            managing_curator=self.manager_user,
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )

        self.artwork = Artwork.objects.create(
            name='Sculpture One',
            end_year=2026,
            created_by=self.artist_user,
            is_public=False,
        )
        self.artwork.artists.add(self.artist_profile)
        self.artwork.shows.add(self.show)

        self.assignment = ShowJuror.objects.create(
            show=self.show,
            user=self.juror_user,
            assigned_by=self.manager_user,
        )

        self.dashboard_url = reverse(
            'reviews:show_review_dashboard', kwargs={'show_slug': self.show.slug}
        )
        self.artwork_review_url = reverse(
            'reviews:artwork_review',
            kwargs={'show_slug': self.show.slug, 'artwork_slug': self.artwork.slug},
        )

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.headers['Location'])

    def test_artist_cannot_access_reviews_dashboard(self):
        self.client.login(username='artist@example.com', password='password123')

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 404)

    def test_unassigned_juror_cannot_access_reviews_dashboard(self):
        self.client.login(username='juror-unassigned@example.com', password='password123')

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 404)

    def test_assigned_juror_can_submit_review(self):
        self.client.login(username='juror@example.com', password='password123')

        get_response = self.client.get(self.artwork_review_url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(
            self.artwork_review_url,
            {'rating': '4', 'body': 'Strong work with clean presentation.'},
            follow=True,
        )

        self.assertEqual(post_response.status_code, 200)
        review = ArtworkReview.objects.get(show=self.show, artwork=self.artwork, juror=self.juror_user)
        self.assertEqual(review.rating, 4)
        self.assertIn('clean presentation', review.body)

    def test_non_managing_curator_cannot_view_artwork_reviews(self):
        self.client.login(username='other-curator@example.com', password='password123')

        response = self.client.get(self.artwork_review_url)

        self.assertEqual(response.status_code, 404)

    def test_managing_curator_can_edit_juror_review(self):
        review = ArtworkReview.objects.create(
            show=self.show,
            artwork=self.artwork,
            juror=self.juror_user,
            rating=2,
            body='Initial notes',
        )
        edit_url = reverse(
            'reviews:curator_edit_review',
            kwargs={
                'show_slug': self.show.slug,
                'artwork_slug': self.artwork.slug,
                'review_id': review.id,
            },
        )

        self.client.login(username='manager@example.com', password='password123')

        get_response = self.client.get(edit_url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(
            edit_url,
            {'rating': '5', 'body': 'Curator adjusted notes.'},
            follow=True,
        )
        self.assertEqual(post_response.status_code, 200)

        review.refresh_from_db()
        self.assertEqual(review.rating, 5)
        self.assertEqual(review.body, 'Curator adjusted notes.')

    def test_managing_curator_can_assign_and_remove_juror(self):
        candidate = User.objects.create_user(
            username='new-juror@example.com',
            email='new-juror@example.com',
            password='password123',
        )
        assignment_url = reverse(
            'reviews:show_juror_assignment', kwargs={'show_slug': self.show.slug}
        )

        self.client.login(username='manager@example.com', password='password123')

        assign_response = self.client.post(
            assignment_url,
            {'action': 'assign', 'user': str(candidate.id)},
            follow=True,
        )
        self.assertEqual(assign_response.status_code, 200)
        self.assertTrue(ShowJuror.objects.filter(show=self.show, user=candidate).exists())
        self.assertTrue(candidate.groups.filter(name='juror').exists())

        candidate_assignment = ShowJuror.objects.get(show=self.show, user=candidate)
        remove_response = self.client.post(
            assignment_url,
            {'action': 'remove', 'assignment_id': str(candidate_assignment.id)},
            follow=True,
        )
        self.assertEqual(remove_response.status_code, 200)
        self.assertFalse(ShowJuror.objects.filter(show=self.show, user=candidate).exists())
