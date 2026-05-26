import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import add_artist_role, add_curator_role, add_juror_role
from gallery.models import Artist, Artwork, ArtworkSubmission, Show
from reviews.models import ArtworkReview, CriterionScore, RubricCriterion, ShowJuror


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
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
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        manager_artist = Artist.objects.create(
            user=self.manager_user,
            name='Manager Artist',
            first_name='Manager',
            last_name='Artist',
            email='manager@example.com',
            phone='',
        )
        self.show.curators.add(manager_artist)

        self.artwork = Artwork.objects.create(
            name='Sculpture One',
            end_year=2026,
            created_by=self.artist_user,
            is_public=False,
        )
        self.artwork.artists.add(self.artist_profile)
        ArtworkSubmission.objects.create(
            show=self.show,
            artwork=self.artwork,
            submitted_by=self.artist_user,
        )

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

    def test_curator_not_on_show_cannot_view_artwork_reviews(self):
        self.client.login(username='other-curator@example.com', password='password123')

        response = self.client.get(self.artwork_review_url)

        self.assertEqual(response.status_code, 404)

    def test_juror_assigned_to_different_show_cannot_review_this_show(self):
        other_show = Show.objects.create(
            name='Other Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        cross_juror = User.objects.create_user(
            username='cross-juror@example.com',
            email='cross-juror@example.com',
            password='password123',
        )
        ShowJuror.objects.create(
            show=other_show,
            user=cross_juror,
            assigned_by=self.manager_user,
        )
        self.client.force_login(cross_juror)

        dashboard_response = self.client.get(self.dashboard_url)
        review_response = self.client.get(self.artwork_review_url)

        self.assertEqual(dashboard_response.status_code, 404)
        self.assertEqual(review_response.status_code, 404)

    def test_curator_not_on_show_cannot_edit_show_submissions(self):
        # ArtworkSubmission already created in setUp
        url = reverse('gallery:show_submissions', kwargs={'slug': self.show.slug})
        self.client.login(username='other-curator@example.com', password='password123')

        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_curator_on_show_can_edit_juror_review(self):
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

    def test_curator_on_show_can_assign_and_remove_juror(self):
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


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class RubricCriteriaTests(TestCase):
    """Tests for rubric criteria management and weighted jury scoring."""

    def setUp(self):
        self.manager_user = User.objects.create_user(
            username='manager@example.com',
            email='manager@example.com',
            password='pw',
        )
        add_curator_role(self.manager_user)

        self.other_curator = User.objects.create_user(
            username='other@example.com',
            email='other@example.com',
            password='pw',
        )
        add_curator_role(self.other_curator)

        self.artist_user = User.objects.create_user(
            username='artist@example.com',
            email='artist@example.com',
            password='pw',
        )
        add_artist_role(self.artist_user)

        self.juror_user = User.objects.create_user(
            username='juror@example.com',
            email='juror@example.com',
            password='pw',
        )

        self.show = Show.objects.create(
            name='Rubric Test Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=7),
        )
        manager_artist = Artist.objects.create(
            user=self.manager_user,
            name='Manager',
            first_name='Manager',
            last_name='User',
            email='manager@example.com',
            phone='',
        )
        self.show.curators.add(manager_artist)

        self.artist_profile = Artist.objects.create(
            user=self.artist_user,
            name='Test Artist',
            first_name='Test',
            last_name='Artist',
            email='artist@example.com',
            phone='',
        )

        self.artwork = Artwork.objects.create(
            name='Test Piece',
            end_year=2026,
            created_by=self.artist_user,
        )
        self.artwork.artists.add(self.artist_profile)
        ArtworkSubmission.objects.create(
            show=self.show,
            artwork=self.artwork,
            submitted_by=self.artist_user,
        )

        self.assignment = ShowJuror.objects.create(
            show=self.show,
            user=self.juror_user,
            assigned_by=self.manager_user,
        )

        self.rubric_url = reverse(
            'reviews:manage_rubric_criteria', kwargs={'show_slug': self.show.slug}
        )

    # --- Access control ---

    def test_anonymous_user_redirected_from_rubric_page(self):
        response = self.client.get(self.rubric_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.headers['Location'])

    def test_curator_not_on_show_cannot_access_rubric_page(self):
        self.client.force_login(self.other_curator)
        response = self.client.get(self.rubric_url)
        self.assertEqual(response.status_code, 404)

    def test_artist_cannot_access_rubric_page(self):
        self.client.force_login(self.artist_user)
        response = self.client.get(self.rubric_url)
        self.assertEqual(response.status_code, 404)

    def test_curator_on_show_can_view_rubric_page(self):
        self.client.force_login(self.manager_user)
        response = self.client.get(self.rubric_url)
        self.assertEqual(response.status_code, 200)

    # --- Creating criteria ---

    def test_curator_can_create_rubric_criterion(self):
        self.client.force_login(self.manager_user)
        response = self.client.post(self.rubric_url, {
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-name': 'Originality',
            'form-0-description': 'How original is the work?',
            'form-0-weight': '2.0',
            'form-0-order': '0',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        criterion = RubricCriterion.objects.get(show=self.show, name='Originality')
        self.assertEqual(criterion.weight, 2.0)
        self.assertEqual(criterion.description, 'How original is the work?')

    def test_curator_can_create_multiple_criteria(self):
        self.client.force_login(self.manager_user)
        self.client.post(self.rubric_url, {
            'form-TOTAL_FORMS': '2',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-name': 'Originality',
            'form-0-description': '',
            'form-0-weight': '2.0',
            'form-0-order': '0',
            'form-1-name': 'Technical Quality',
            'form-1-description': '',
            'form-1-weight': '1.0',
            'form-1-order': '10',
        })

        self.assertEqual(RubricCriterion.objects.filter(show=self.show).count(), 2)

    def test_curator_can_delete_criterion(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='To Delete', weight=1.0, order=0
        )
        self.client.force_login(self.manager_user)
        self.client.post(self.rubric_url, {
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '1',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-id': str(criterion.pk),
            'form-0-name': 'To Delete',
            'form-0-description': '',
            'form-0-weight': '1.0',
            'form-0-order': '0',
            'form-0-DELETE': 'on',
        })

        self.assertFalse(RubricCriterion.objects.filter(pk=criterion.pk).exists())

    # --- Juror scoring with criteria ---

    def test_review_form_shows_criterion_fields_when_rubric_defined(self):
        RubricCriterion.objects.create(show=self.show, name='Originality', weight=1.0, order=0)
        self.client.force_login(self.juror_user)

        response = self.client.get(reverse(
            'reviews:artwork_review',
            kwargs={'show_slug': self.show.slug, 'artwork_slug': self.artwork.slug},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Originality')

    def test_juror_can_submit_criterion_scores(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='Originality', weight=1.0, order=0
        )
        self.client.force_login(self.juror_user)

        self.client.post(
            reverse('reviews:artwork_review',
                    kwargs={'show_slug': self.show.slug, 'artwork_slug': self.artwork.slug}),
            {
                f'criterion_{criterion.pk}': '8',
                'rating': '',
                'body': 'Great work.',
            },
            follow=True,
        )

        review = ArtworkReview.objects.get(show=self.show, artwork=self.artwork, juror=self.juror_user)
        score = CriterionScore.objects.get(review=review, criterion=criterion)
        self.assertEqual(score.score, 8)

    def test_juror_can_update_criterion_score(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='Originality', weight=1.0, order=0
        )
        review = ArtworkReview.objects.create(
            show=self.show, artwork=self.artwork, juror=self.juror_user, body='first'
        )
        CriterionScore.objects.create(review=review, criterion=criterion, score=5)

        self.client.force_login(self.juror_user)
        self.client.post(
            reverse('reviews:artwork_review',
                    kwargs={'show_slug': self.show.slug, 'artwork_slug': self.artwork.slug}),
            {f'criterion_{criterion.pk}': '9', 'rating': '', 'body': 'updated'},
            follow=True,
        )

        score = CriterionScore.objects.get(review=review, criterion=criterion)
        self.assertEqual(score.score, 9)

    def test_criterion_score_is_required_when_rubric_defined(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='Originality', weight=1.0, order=0
        )
        self.client.force_login(self.juror_user)

        response = self.client.post(
            reverse('reviews:artwork_review',
                    kwargs={'show_slug': self.show.slug, 'artwork_slug': self.artwork.slug}),
            {'rating': '', 'body': 'Missing criterion score.'},
        )

        # Form invalid — no review created
        self.assertFalse(ArtworkReview.objects.filter(
            show=self.show, artwork=self.artwork, juror=self.juror_user
        ).exists())

    # --- Weighted score computation ---

    def test_dashboard_shows_weighted_score_when_criteria_defined(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='Originality', weight=1.0, order=0
        )
        review = ArtworkReview.objects.create(
            show=self.show, artwork=self.artwork, juror=self.juror_user
        )
        CriterionScore.objects.create(review=review, criterion=criterion, score=8)

        self.client.force_login(self.manager_user)
        response = self.client.get(
            reverse('reviews:show_review_dashboard', kwargs={'show_slug': self.show.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Weighted Score')
        self.assertContains(response, '8.00/10')

    def test_weighted_score_uses_criterion_weights(self):
        """Two criteria with weights 2 and 1; scores 6 and 9 → weighted avg = (6*2 + 9*1)/3 = 7.0"""
        c1 = RubricCriterion.objects.create(show=self.show, name='Concept', weight=2.0, order=0)
        c2 = RubricCriterion.objects.create(show=self.show, name='Craft', weight=1.0, order=1)
        review = ArtworkReview.objects.create(
            show=self.show, artwork=self.artwork, juror=self.juror_user
        )
        CriterionScore.objects.create(review=review, criterion=c1, score=6)
        CriterionScore.objects.create(review=review, criterion=c2, score=9)

        self.client.force_login(self.manager_user)
        response = self.client.get(
            reverse('reviews:show_review_dashboard', kwargs={'show_slug': self.show.slug})
        )

        self.assertContains(response, '7.00/10')

    def test_dashboard_shows_avg_rating_when_no_criteria(self):
        ArtworkReview.objects.create(
            show=self.show, artwork=self.artwork, juror=self.juror_user, rating=7
        )

        self.client.force_login(self.manager_user)
        response = self.client.get(
            reverse('reviews:show_review_dashboard', kwargs={'show_slug': self.show.slug})
        )

        self.assertContains(response, 'Avg Rating')
        self.assertContains(response, '7.0/10')

    # --- Curator edit with criteria ---

    def test_curator_can_edit_juror_criterion_score(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='Originality', weight=1.0, order=0
        )
        review = ArtworkReview.objects.create(
            show=self.show, artwork=self.artwork, juror=self.juror_user, body='initial'
        )
        CriterionScore.objects.create(review=review, criterion=criterion, score=4)

        edit_url = reverse('reviews:curator_edit_review', kwargs={
            'show_slug': self.show.slug,
            'artwork_slug': self.artwork.slug,
            'review_id': review.pk,
        })
        self.client.force_login(self.manager_user)
        self.client.post(edit_url, {
            f'criterion_{criterion.pk}': '9',
            'rating': '',
            'body': 'curator adjusted',
        }, follow=True)

        score = CriterionScore.objects.get(review=review, criterion=criterion)
        self.assertEqual(score.score, 9)

    # --- Dashboard criterion columns ---

    def test_dashboard_shows_criterion_columns_in_all_reviews_table(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='Presentation', weight=1.0, order=0
        )
        review = ArtworkReview.objects.create(
            show=self.show, artwork=self.artwork, juror=self.juror_user
        )
        CriterionScore.objects.create(review=review, criterion=criterion, score=7)

        self.client.force_login(self.manager_user)
        response = self.client.get(
            reverse('reviews:show_review_dashboard', kwargs={'show_slug': self.show.slug})
        )

        self.assertContains(response, 'Presentation')
        self.assertContains(response, '7')
