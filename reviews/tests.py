import datetime
import json

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from gallery.models import Artist, Artwork, ArtworkSubmission, Show, ShowArtworkNumber
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

        self.other_curator_user = User.objects.create_user(
            username='other-curator@example.com',
            email='other-curator@example.com',
            password='password123',
        )

        self.artist_user = User.objects.create_user(
            username='artist@example.com',
            email='artist@example.com',
            password='password123',
        )

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
            {'rating': '75', 'body': 'Strong work with clean presentation.'},
            follow=True,
        )

        self.assertEqual(post_response.status_code, 200)
        review = ArtworkReview.objects.get(show=self.show, artwork=self.artwork, juror=self.juror_user)
        self.assertEqual(review.rating, 75)
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
            rating=20,
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
            {'rating': '50', 'body': 'Curator adjusted notes.'},
            follow=True,
        )
        self.assertEqual(post_response.status_code, 200)

        review.refresh_from_db()
        self.assertEqual(review.rating, 50)
        self.assertEqual(review.body, 'Curator adjusted notes.')

    def test_curator_on_show_can_assign_and_remove_juror(self):
        candidate = User.objects.create_user(
            username='new-juror@example.com',
            email='new-juror@example.com',
            password='password123',
        )
        candidate_artist = Artist.objects.create(
            name='New Juror',
            first_name='New',
            last_name='Juror',
            email='new-juror@example.com',
            phone='',
            user=candidate,
        )
        assignment_url = reverse(
            'reviews:show_juror_assignment', kwargs={'show_slug': self.show.slug}
        )

        self.client.login(username='manager@example.com', password='password123')

        assign_response = self.client.post(
            assignment_url,
            {'action': 'assign', 'artist': str(candidate_artist.id)},
            follow=True,
        )
        self.assertEqual(assign_response.status_code, 200)
        self.assertTrue(ShowJuror.objects.filter(show=self.show, user=candidate).exists())

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

        self.other_curator = User.objects.create_user(
            username='other@example.com',
            email='other@example.com',
            password='pw',
        )

        self.artist_user = User.objects.create_user(
            username='artist@example.com',
            email='artist@example.com',
            password='pw',
        )

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
            'form-0-percentage': '60',
            'form-0-order': '0',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        criterion = RubricCriterion.objects.get(show=self.show, name='Originality')
        self.assertEqual(criterion.percentage, 60.0)
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
            'form-0-percentage': '60',
            'form-0-order': '0',
            'form-1-name': 'Technical Quality',
            'form-1-description': '',
            'form-1-percentage': '40',
            'form-1-order': '10',
        })

        self.assertEqual(RubricCriterion.objects.filter(show=self.show).count(), 2)

    def test_curator_can_delete_criterion(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='To Delete', percentage=100.0, order=0
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
            'form-0-percentage': '100',
            'form-0-order': '0',
            'form-0-DELETE': 'on',
        })

        self.assertFalse(RubricCriterion.objects.filter(pk=criterion.pk).exists())

    # --- Juror scoring with criteria ---

    def test_review_form_shows_criterion_fields_when_rubric_defined(self):
        RubricCriterion.objects.create(show=self.show, name='Originality', percentage=100.0, order=0)
        self.client.force_login(self.juror_user)

        response = self.client.get(reverse(
            'reviews:artwork_review',
            kwargs={'show_slug': self.show.slug, 'artwork_slug': self.artwork.slug},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Originality')

    def test_juror_can_submit_criterion_scores(self):
        criterion = RubricCriterion.objects.create(
            show=self.show, name='Originality', percentage=100.0, order=0
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
            show=self.show, name='Originality', percentage=100.0, order=0
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
            show=self.show, name='Originality', percentage=100.0, order=0
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
            show=self.show, name='Originality', percentage=100.0, order=0
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
        self.assertContains(response, '8.00/100')

    def test_weighted_score_uses_criterion_percentages(self):
        """Two criteria with percentages 60 and 40; scores 60 and 90 → 60*0.6 + 90*0.4 = 72.0"""
        c1 = RubricCriterion.objects.create(show=self.show, name='Concept', percentage=60.0, order=0)
        c2 = RubricCriterion.objects.create(show=self.show, name='Craft', percentage=40.0, order=1)
        review = ArtworkReview.objects.create(
            show=self.show, artwork=self.artwork, juror=self.juror_user
        )
        CriterionScore.objects.create(review=review, criterion=c1, score=60)
        CriterionScore.objects.create(review=review, criterion=c2, score=90)

        self.client.force_login(self.manager_user)
        response = self.client.get(
            reverse('reviews:show_review_dashboard', kwargs={'show_slug': self.show.slug})
        )

        self.assertContains(response, '72.00/100')

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
            show=self.show, name='Originality', percentage=100.0, order=0
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
            show=self.show, name='Presentation', percentage=100.0, order=0
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


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class OpenCallJuryWorkflowTests(TestCase):
    """End-to-end tests for the open call jury → curation → publish workflow."""

    def setUp(self):
        # Curator
        self.curator_user = User.objects.create_user(
            username='curator@example.com', email='curator@example.com', password='pw',
        )
        self.curator_artist = Artist.objects.create(
            user=self.curator_user, name='Curator Person',
            first_name='Curator', last_name='Person', email='curator@example.com', phone='',
        )

        # Two jurors
        self.juror1 = User.objects.create_user(
            username='juror1@example.com', email='juror1@example.com', password='pw',
        )
        self.juror2 = User.objects.create_user(
            username='juror2@example.com', email='juror2@example.com', password='pw',
        )

        # Three submitting artists
        self.artist1_user = User.objects.create_user(
            username='artist1@example.com', email='artist1@example.com', password='pw',
        )
        self.artist1 = Artist.objects.create(
            user=self.artist1_user, name='Artist One',
            first_name='Artist', last_name='One', email='artist1@example.com', phone='',
        )
        self.artist2_user = User.objects.create_user(
            username='artist2@example.com', email='artist2@example.com', password='pw',
        )
        self.artist2 = Artist.objects.create(
            user=self.artist2_user, name='Artist Two',
            first_name='Artist', last_name='Two', email='artist2@example.com', phone='',
        )
        self.artist3_user = User.objects.create_user(
            username='artist3@example.com', email='artist3@example.com', password='pw',
        )
        self.artist3 = Artist.objects.create(
            user=self.artist3_user, name='Artist Three',
            first_name='Artist', last_name='Three', email='artist3@example.com', phone='',
        )

        # Show in Open Call state
        self.show = Show.objects.create(
            name='Jury Test Show',
            start=datetime.date.today(),
            end=datetime.date.today() + datetime.timedelta(days=30),
            status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_OPEN,
            submission_deadline=datetime.date.today() + datetime.timedelta(days=7),
        )
        self.show.curators.add(self.curator_artist)

        # Assign both jurors
        ShowJuror.objects.create(show=self.show, user=self.juror1, assigned_by=self.curator_user)
        ShowJuror.objects.create(show=self.show, user=self.juror2, assigned_by=self.curator_user)

        # Rubric: two criteria (60/40 split)
        self.criterion_orig = RubricCriterion.objects.create(
            show=self.show, name='Originality', percentage=60.0, order=0,
        )
        self.criterion_exec = RubricCriterion.objects.create(
            show=self.show, name='Execution', percentage=40.0, order=1,
        )

        # Artworks
        self.artwork1 = Artwork.objects.create(
            name='Piece One', end_year=2026, created_by=self.artist1_user,
        )
        self.artwork1.artists.add(self.artist1)
        self.artwork2 = Artwork.objects.create(
            name='Piece Two', end_year=2026, created_by=self.artist2_user,
        )
        self.artwork2.artists.add(self.artist2)
        self.artwork3 = Artwork.objects.create(
            name='Piece Three', end_year=2026, created_by=self.artist3_user,
        )
        self.artwork3.artists.add(self.artist3)

        # Submissions
        self.sub1 = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork1, submitted_by=self.artist1_user,
        )
        self.sub2 = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork2, submitted_by=self.artist2_user,
        )
        self.sub3 = ArtworkSubmission.objects.create(
            show=self.show, artwork=self.artwork3, submitted_by=self.artist3_user,
        )

        # URL helpers
        self.save_score_url = reverse('reviews:save_score', kwargs={'show_slug': self.show.slug})
        self.save_decision_url = reverse('reviews:save_decision', kwargs={'show_slug': self.show.slug})
        self.curation_data_url = reverse('reviews:curation_data', kwargs={'show_slug': self.show.slug})
        self.promote_url = reverse('gallery:promote_artworks', kwargs={'slug': self.show.slug})

    def _score_artwork(self, user, artwork, orig_score, exec_score):
        """Post both criterion scores for one artwork as the given juror."""
        self.client.force_login(user)
        self.client.post(
            self.save_score_url,
            data=json.dumps({
                'artwork_slug': artwork.slug,
                'criterion_id': self.criterion_orig.pk,
                'score': orig_score,
            }),
            content_type='application/json',
        )
        self.client.post(
            self.save_score_url,
            data=json.dumps({
                'artwork_slug': artwork.slug,
                'criterion_id': self.criterion_exec.pk,
                'score': exec_score,
            }),
            content_type='application/json',
        )

    # --- save_score API ---

    def test_save_score_creates_review_and_criterion_score(self):
        self.client.force_login(self.juror1)
        response = self.client.post(
            self.save_score_url,
            data=json.dumps({
                'artwork_slug': self.artwork1.slug,
                'criterion_id': self.criterion_orig.pk,
                'score': 80,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'ok': True})
        review = ArtworkReview.objects.get(show=self.show, artwork=self.artwork1, juror=self.juror1)
        score = CriterionScore.objects.get(review=review, criterion=self.criterion_orig)
        self.assertEqual(score.score, 80)

    def test_save_score_updates_existing_score(self):
        self._score_artwork(self.juror1, self.artwork1, orig_score=60, exec_score=70)
        self.client.force_login(self.juror1)
        self.client.post(
            self.save_score_url,
            data=json.dumps({
                'artwork_slug': self.artwork1.slug,
                'criterion_id': self.criterion_orig.pk,
                'score': 90,
            }),
            content_type='application/json',
        )
        review = ArtworkReview.objects.get(show=self.show, artwork=self.artwork1, juror=self.juror1)
        score = CriterionScore.objects.get(review=review, criterion=self.criterion_orig)
        self.assertEqual(score.score, 90)
        self.assertEqual(
            ArtworkReview.objects.filter(show=self.show, artwork=self.artwork1, juror=self.juror1).count(),
            1,
        )

    def test_unassigned_user_cannot_save_score(self):
        outsider = User.objects.create_user(
            username='outsider@x.com', email='outsider@x.com', password='pw',
        )
        self.client.force_login(outsider)
        response = self.client.post(
            self.save_score_url,
            data=json.dumps({
                'artwork_slug': self.artwork1.slug,
                'criterion_id': self.criterion_orig.pk,
                'score': 80,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ArtworkReview.objects.filter(show=self.show, artwork=self.artwork1).exists())

    # --- save_decision API ---

    def test_save_decision_marks_submission_selected(self):
        self.client.force_login(self.curator_user)
        response = self.client.post(
            self.save_decision_url,
            data=json.dumps({
                'submission_id': self.sub1.pk,
                'decision': ArtworkSubmission.CURATOR_SELECTED,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.sub1.refresh_from_db()
        self.assertEqual(self.sub1.curator_decision, ArtworkSubmission.CURATOR_SELECTED)

    def test_save_decision_marks_submission_rejected(self):
        self.client.force_login(self.curator_user)
        self.client.post(
            self.save_decision_url,
            data=json.dumps({
                'submission_id': self.sub2.pk,
                'decision': ArtworkSubmission.CURATOR_REJECTED,
            }),
            content_type='application/json',
        )
        self.sub2.refresh_from_db()
        self.assertEqual(self.sub2.curator_decision, ArtworkSubmission.CURATOR_REJECTED)

    def test_save_decision_marks_submission_withdrawn(self):
        self.client.force_login(self.curator_user)
        response = self.client.post(
            self.save_decision_url,
            data=json.dumps({
                'submission_id': self.sub1.pk,
                'decision': ArtworkSubmission.WITHDRAWN,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.sub1.refresh_from_db()
        self.assertEqual(self.sub1.curator_decision, ArtworkSubmission.WITHDRAWN)

    def test_non_curator_cannot_save_decision(self):
        self.client.force_login(self.juror1)
        response = self.client.post(
            self.save_decision_url,
            data=json.dumps({
                'submission_id': self.sub1.pk,
                'decision': ArtworkSubmission.CURATOR_SELECTED,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    # --- curation_data API ---

    def test_curation_data_returns_all_submissions(self):
        self.client.force_login(self.curator_user)
        response = self.client.get(self.curation_data_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        submission_ids = {a['submission_id'] for a in data['artworks']}
        self.assertIn(self.sub1.pk, submission_ids)
        self.assertIn(self.sub2.pk, submission_ids)
        self.assertIn(self.sub3.pk, submission_ids)

    def test_curation_data_decision_filter(self):
        """?decision=undecided returns only undecided submissions."""
        self.sub1.curator_decision = ArtworkSubmission.CURATOR_SELECTED
        self.sub1.save(update_fields=['curator_decision'])
        self.sub2.curator_decision = ArtworkSubmission.CURATOR_REJECTED
        self.sub2.save(update_fields=['curator_decision'])
        # sub3 stays UNDECIDED

        self.client.force_login(self.curator_user)
        data = self.client.get(self.curation_data_url + '?decision=undecided').json()
        submission_ids = {a['submission_id'] for a in data['artworks']}
        self.assertNotIn(self.sub1.pk, submission_ids)
        self.assertNotIn(self.sub2.pk, submission_ids)
        self.assertIn(self.sub3.pk, submission_ids)

    # --- Multi-juror scoring ---

    def test_two_jurors_scores_stored_independently(self):
        self._score_artwork(self.juror1, self.artwork1, orig_score=80, exec_score=70)
        self._score_artwork(self.juror2, self.artwork1, orig_score=60, exec_score=50)
        reviews = ArtworkReview.objects.filter(show=self.show, artwork=self.artwork1)
        self.assertEqual(reviews.count(), 2)
        j1_review = reviews.get(juror=self.juror1)
        j2_review = reviews.get(juror=self.juror2)
        self.assertEqual(
            j1_review.criterion_scores.get(criterion=self.criterion_orig).score, 80,
        )
        self.assertEqual(
            j2_review.criterion_scores.get(criterion=self.criterion_orig).score, 60,
        )

    # --- promote ---

    def test_promote_adds_selected_not_rejected_not_undecided(self):
        self.sub1.curator_decision = ArtworkSubmission.CURATOR_SELECTED
        self.sub1.save(update_fields=['curator_decision'])
        self.sub2.curator_decision = ArtworkSubmission.CURATOR_REJECTED
        self.sub2.save(update_fields=['curator_decision'])
        # sub3 also rejected — promote blocks if any submission is still undecided
        self.sub3.curator_decision = ArtworkSubmission.CURATOR_REJECTED
        self.sub3.save(update_fields=['curator_decision'])

        self.client.force_login(self.curator_user)
        self.client.post(self.promote_url)

        self.assertTrue(self.show.artworks.filter(pk=self.artwork1.pk).exists())
        self.assertFalse(self.show.artworks.filter(pk=self.artwork2.pk).exists())
        self.assertFalse(self.show.artworks.filter(pk=self.artwork3.pk).exists())

    # --- status transition emails ---

    def test_in_review_transition_emails_both_jurors(self):
        self.client.force_login(self.curator_user)
        self.client.post(
            reverse('gallery:transition_show_status', kwargs={'pk': self.show.pk}),
            {'status': Show.STATUS_IN_REVIEW},
        )
        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_IN_REVIEW)
        recipients = {addr for msg in mail.outbox for addr in msg.recipients()}
        self.assertIn(self.juror1.email, recipients)
        self.assertIn(self.juror2.email, recipients)

    # --- Full end-to-end flow ---

    def test_full_jury_and_curation_workflow(self):
        """Score → decide → promote from DRAFT → publish → correct emails."""
        # Both jurors score all three artworks
        self._score_artwork(self.juror1, self.artwork1, orig_score=90, exec_score=80)
        self._score_artwork(self.juror1, self.artwork2, orig_score=50, exec_score=40)
        self._score_artwork(self.juror1, self.artwork3, orig_score=30, exec_score=20)
        self._score_artwork(self.juror2, self.artwork1, orig_score=70, exec_score=60)
        self._score_artwork(self.juror2, self.artwork2, orig_score=60, exec_score=50)
        self._score_artwork(self.juror2, self.artwork3, orig_score=20, exec_score=10)

        self.assertEqual(ArtworkReview.objects.filter(show=self.show).count(), 6)
        self.assertEqual(CriterionScore.objects.filter(review__show=self.show).count(), 12)

        # Curator makes decisions
        self.client.force_login(self.curator_user)
        for sub, decision in [
            (self.sub1, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub2, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub3, ArtworkSubmission.CURATOR_REJECTED),
        ]:
            self.client.post(
                self.save_decision_url,
                data=json.dumps({'submission_id': sub.pk, 'decision': decision}),
                content_type='application/json',
            )

        # Promote from DRAFT → publishes and sends emails
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])
        mail.outbox.clear()

        self.client.post(self.promote_url)

        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_PUBLISHED)

        # Selected artworks in show, rejected not
        self.assertTrue(self.show.artworks.filter(pk=self.artwork1.pk).exists())
        self.assertTrue(self.show.artworks.filter(pk=self.artwork2.pk).exists())
        self.assertFalse(self.show.artworks.filter(pk=self.artwork3.pk).exists())

        # One email per submitting artist (accept or reject)
        self.assertEqual(len(mail.outbox), 3)
        all_recipients = {addr for msg in mail.outbox for addr in msg.recipients()}
        self.assertIn(self.artist1_user.email, all_recipients)
        self.assertIn(self.artist2_user.email, all_recipients)
        self.assertIn(self.artist3_user.email, all_recipients)

    # =========================================================================
    # Weighted score computation
    # =========================================================================

    def test_weighted_score_computed_correctly(self):
        """Two jurors × two criteria: weighted avg = sum(avg_per_crit * pct/100)."""
        # juror1: orig=80, exec=60 → weighted = 80*0.6 + 60*0.4 = 72
        # juror2: orig=60, exec=40 → weighted = 60*0.6 + 40*0.4 = 52
        # average weighted across jurors: avg_orig=(80+60)/2=70, avg_exec=(60+40)/2=50
        # final = 70*0.6 + 50*0.4 = 42 + 20 = 62.0
        self._score_artwork(self.juror1, self.artwork1, orig_score=80, exec_score=60)
        self._score_artwork(self.juror2, self.artwork1, orig_score=60, exec_score=40)

        self.client.force_login(self.curator_user)
        data = self.client.get(self.curation_data_url).json()
        a1_entry = next(a for a in data['artworks'] if a['slug'] == self.artwork1.slug)
        self.assertEqual(a1_entry['weighted_score'], 62.0)

    def test_single_juror_weighted_score(self):
        """Single juror: weighted score = score * pct/100 (no averaging needed)."""
        # orig=100, exec=50 → 100*0.6 + 50*0.4 = 60 + 20 = 80.0
        self._score_artwork(self.juror1, self.artwork1, orig_score=100, exec_score=50)

        self.client.force_login(self.curator_user)
        data = self.client.get(self.curation_data_url).json()
        a1_entry = next(a for a in data['artworks'] if a['slug'] == self.artwork1.slug)
        self.assertEqual(a1_entry['weighted_score'], 80.0)

    def test_unscored_artwork_has_null_weighted_score(self):
        """An artwork with no scores returns weighted_score=null in curation_data."""
        self.client.force_login(self.curator_user)
        data = self.client.get(self.curation_data_url).json()
        a1_entry = next(a for a in data['artworks'] if a['slug'] == self.artwork1.slug)
        self.assertIsNone(a1_entry['weighted_score'])

    def test_curation_data_sorted_by_weighted_score_descending(self):
        """Artwork with higher weighted score appears first in curation_data response."""
        # artwork1: high scores; artwork2: low scores
        self._score_artwork(self.juror1, self.artwork1, orig_score=90, exec_score=90)
        self._score_artwork(self.juror1, self.artwork2, orig_score=20, exec_score=20)

        self.client.force_login(self.curator_user)
        data = self.client.get(self.curation_data_url).json()
        scored = [a for a in data['artworks'] if a['weighted_score'] is not None]
        scores = [a['weighted_score'] for a in scored]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(scored[0]['slug'], self.artwork1.slug)

    def test_curation_data_juror_scores_structure(self):
        """Each juror entry in juror_scores contains their per-criterion scores."""
        self._score_artwork(self.juror1, self.artwork1, orig_score=75, exec_score=65)

        self.client.force_login(self.curator_user)
        data = self.client.get(self.curation_data_url).json()
        a1 = next(a for a in data['artworks'] if a['slug'] == self.artwork1.slug)
        self.assertEqual(len(a1['juror_scores']), 1)
        j_entry = a1['juror_scores'][0]
        self.assertIn('criteria', j_entry)
        self.assertIn('weighted', j_entry)
        crit_scores = j_entry['criteria']
        self.assertEqual(crit_scores[str(self.criterion_orig.pk)], 75)
        self.assertEqual(crit_scores[str(self.criterion_exec.pk)], 65)
        self.assertAlmostEqual(j_entry['weighted'], 75 * 0.6 + 65 * 0.4, places=1)

    # =========================================================================
    # Email content
    # =========================================================================

    def _promote_all_decided(self, sub1_decision, sub2_decision, sub3_decision,
                             from_status=Show.STATUS_DRAFT):
        """Helper: set decisions and promote from the given status."""
        for sub, dec in [(self.sub1, sub1_decision), (self.sub2, sub2_decision),
                         (self.sub3, sub3_decision)]:
            sub.curator_decision = dec
            sub.save(update_fields=['curator_decision'])
        self.show.status = from_status
        self.show.save(update_fields=['status'])
        mail.outbox.clear()
        self.client.force_login(self.curator_user)
        self.client.post(self.promote_url)

    def test_acceptance_email_subject_contains_show_name(self):
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_SELECTED,
            ArtworkSubmission.CURATOR_REJECTED,
            ArtworkSubmission.CURATOR_REJECTED,
        )
        accepted_msgs = [m for m in mail.outbox if self.artist1_user.email in m.recipients()]
        self.assertTrue(accepted_msgs, 'No email sent to accepted artist')
        self.assertIn(self.show.name, accepted_msgs[0].subject)

    def test_rejection_email_subject_contains_show_name(self):
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_REJECTED,
            ArtworkSubmission.CURATOR_SELECTED,
            ArtworkSubmission.CURATOR_REJECTED,
        )
        rejected_msgs = [m for m in mail.outbox if self.artist1_user.email in m.recipients()]
        self.assertTrue(rejected_msgs, 'No email sent to rejected artist')
        self.assertIn(self.show.name, rejected_msgs[0].subject)

    def test_accepted_artist_not_rejected_artist_gets_acceptance_email(self):
        """Accepted artist receives the 'selected' subject; rejected artist does not."""
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_SELECTED,   # artist1 → accepted
            ArtworkSubmission.CURATOR_REJECTED,   # artist2 → rejected
            ArtworkSubmission.CURATOR_REJECTED,
        )
        msgs_by_recipient = {m.recipients()[0]: m for m in mail.outbox if m.recipients()}
        accept_subj = f'Your work has been selected for {self.show.name}'
        reject_subj = f'Update on your submission to {self.show.name}'
        self.assertEqual(msgs_by_recipient[self.artist1_user.email].subject, accept_subj)
        self.assertEqual(msgs_by_recipient[self.artist2_user.email].subject, reject_subj)

    def test_juror_does_not_receive_acceptance_rejection_emails(self):
        """Jurors are not emailed when artworks are accepted/rejected."""
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_SELECTED,
            ArtworkSubmission.CURATOR_REJECTED,
            ArtworkSubmission.CURATOR_REJECTED,
        )
        all_recipients = {addr for m in mail.outbox for addr in m.recipients()}
        self.assertNotIn(self.juror1.email, all_recipients)
        self.assertNotIn(self.juror2.email, all_recipients)

    @override_settings(GALLERY_SELECTION_CC_EMAIL='gallery@example.com')
    def test_selection_emails_cc_gallery_address(self):
        """Each acceptance/rejection email is CC'd to the configured gallery address."""
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_SELECTED,
            ArtworkSubmission.CURATOR_REJECTED,
            ArtworkSubmission.CURATOR_REJECTED,
        )
        for msg in mail.outbox:
            self.assertIn('gallery@example.com', msg.cc,
                          f'CC missing on email to {msg.to}')

    @override_settings(GALLERY_SELECTION_CC_EMAIL=None)
    def test_selection_emails_cc_curator(self):
        """Each acceptance/rejection email is CC'd to the show's curator."""
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_SELECTED,
            ArtworkSubmission.CURATOR_REJECTED,
            ArtworkSubmission.CURATOR_REJECTED,
        )
        for msg in mail.outbox:
            self.assertIn(self.curator_user.email, msg.cc,
                          f'Curator CC missing on email to {msg.to}')

    def test_juror_email_subject_contains_show_name(self):
        """Email sent to jurors on IN_REVIEW transition contains the show name."""
        self.client.force_login(self.curator_user)
        self.client.post(
            reverse('gallery:transition_show_status', kwargs={'pk': self.show.pk}),
            {'status': Show.STATUS_IN_REVIEW},
        )
        juror_msgs = [m for m in mail.outbox if self.juror1.email in m.recipients()]
        self.assertTrue(juror_msgs)
        self.assertIn(self.show.name, juror_msgs[0].subject)

    # =========================================================================
    # Promote edge cases
    # =========================================================================

    def test_promote_blocked_when_any_submission_undecided(self):
        """If any submission is still undecided the promote POST does nothing."""
        self.sub1.curator_decision = ArtworkSubmission.CURATOR_SELECTED
        self.sub1.save(update_fields=['curator_decision'])
        self.sub2.curator_decision = ArtworkSubmission.CURATOR_REJECTED
        self.sub2.save(update_fields=['curator_decision'])
        # sub3 stays UNDECIDED

        self.client.force_login(self.curator_user)
        self.client.post(self.promote_url)

        self.assertFalse(self.show.artworks.filter(pk=self.artwork1.pk).exists())
        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_OPEN_CALL)

    def test_promote_from_open_call_does_not_publish(self):
        """Promoting from OPEN_CALL adds artworks but keeps the show unpublished."""
        for sub, dec in [
            (self.sub1, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub2, ArtworkSubmission.CURATOR_REJECTED),
            (self.sub3, ArtworkSubmission.CURATOR_REJECTED),
        ]:
            sub.curator_decision = dec
            sub.save(update_fields=['curator_decision'])
        # show is STATUS_OPEN_CALL (default from setUp)
        mail.outbox.clear()
        self.client.force_login(self.curator_user)
        self.client.post(self.promote_url)

        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_OPEN_CALL)
        self.assertTrue(self.show.artworks.filter(pk=self.artwork1.pk).exists())
        # No acceptance/rejection emails sent (only from DRAFT→PUBLISHED)
        self.assertEqual(len(mail.outbox), 0)

    def test_promote_twice_does_not_duplicate_artwork_numbers(self):
        """Re-promoting an already-added artwork does not create a duplicate number."""
        for sub, dec in [
            (self.sub1, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub2, ArtworkSubmission.CURATOR_REJECTED),
            (self.sub3, ArtworkSubmission.CURATOR_REJECTED),
        ]:
            sub.curator_decision = dec
            sub.save(update_fields=['curator_decision'])

        self.client.force_login(self.curator_user)
        self.client.post(self.promote_url)
        self.client.post(self.promote_url)  # second promote

        count = ShowArtworkNumber.objects.filter(show=self.show, artwork=self.artwork1).count()
        self.assertEqual(count, 1)

    def test_artwork_numbers_assigned_on_promote(self):
        """Selected artworks receive sequential ShowArtworkNumber entries."""
        for sub, dec in [
            (self.sub1, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub2, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub3, ArtworkSubmission.CURATOR_REJECTED),
        ]:
            sub.curator_decision = dec
            sub.save(update_fields=['curator_decision'])

        self.client.force_login(self.curator_user)
        self.client.post(self.promote_url)

        nums = ShowArtworkNumber.objects.filter(show=self.show).order_by('number')
        self.assertEqual(nums.count(), 2)
        artwork_ids = {n.artwork_id for n in nums}
        self.assertIn(self.artwork1.pk, artwork_ids)
        self.assertIn(self.artwork2.pk, artwork_ids)

    def test_submission_status_set_to_accepted_after_promote(self):
        """ArtworkSubmission.status is ACCEPTED for selected subs after promote."""
        for sub, dec in [
            (self.sub1, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub2, ArtworkSubmission.CURATOR_REJECTED),
            (self.sub3, ArtworkSubmission.CURATOR_REJECTED),
        ]:
            sub.curator_decision = dec
            sub.save(update_fields=['curator_decision'])

        self.client.force_login(self.curator_user)
        self.client.post(self.promote_url)

        self.sub1.refresh_from_db()
        self.sub2.refresh_from_db()
        self.assertEqual(self.sub1.status, ArtworkSubmission.ACCEPTED)
        self.assertEqual(self.sub2.status, ArtworkSubmission.REJECTED)

    # =========================================================================
    # Public visibility after publishing
    # =========================================================================

    def test_published_show_artworks_visible_to_anonymous(self):
        """After promote from DRAFT the show is published and artworks are accessible."""
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_SELECTED,
            ArtworkSubmission.CURATOR_REJECTED,
            ArtworkSubmission.CURATOR_REJECTED,
        )
        self.show.refresh_from_db()
        self.assertEqual(self.show.status, Show.STATUS_PUBLISHED)

        self.client.logout()
        response = self.client.get(
            reverse('gallery:show_detail', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.artwork1.name)

    def test_rejected_artwork_not_in_published_show(self):
        """Rejected artwork does not appear on the public show detail page."""
        self._promote_all_decided(
            ArtworkSubmission.CURATOR_SELECTED,
            ArtworkSubmission.CURATOR_REJECTED,
            ArtworkSubmission.CURATOR_REJECTED,
        )
        self.client.logout()
        response = self.client.get(
            reverse('gallery:show_detail', kwargs={'slug': self.show.slug})
        )
        # artwork2 was rejected — it must not be in show.artworks
        self.assertFalse(self.show.artworks.filter(pk=self.artwork2.pk).exists())

    def test_draft_show_not_visible_to_anonymous(self):
        """A show in DRAFT status is not accessible to the public (returns 404)."""
        self.show.status = Show.STATUS_DRAFT
        self.show.save(update_fields=['status'])

        self.client.logout()
        response = self.client.get(
            reverse('gallery:show_detail', kwargs={'slug': self.show.slug})
        )
        self.assertEqual(response.status_code, 404)

    # =========================================================================
    # Permission checks
    # =========================================================================

    def test_juror_cannot_get_promote_page(self):
        """A juror has no access to the promote_artworks page."""
        self.client.force_login(self.juror1)
        response = self.client.get(self.promote_url)
        self.assertEqual(response.status_code, 404)

    def test_juror_cannot_post_to_promote(self):
        """A juror cannot POST to promote_artworks."""
        for sub, dec in [
            (self.sub1, ArtworkSubmission.CURATOR_SELECTED),
            (self.sub2, ArtworkSubmission.CURATOR_REJECTED),
            (self.sub3, ArtworkSubmission.CURATOR_REJECTED),
        ]:
            sub.curator_decision = dec
            sub.save(update_fields=['curator_decision'])

        self.client.force_login(self.juror1)
        self.client.post(self.promote_url)
        self.assertFalse(self.show.artworks.exists())

    def test_unauthenticated_cannot_post_to_save_score(self):
        """Unauthenticated request to save_score gets redirected to login."""
        self.client.logout()
        response = self.client.post(
            self.save_score_url,
            data=json.dumps({'artwork_slug': self.artwork1.slug,
                             'criterion_id': self.criterion_orig.pk, 'score': 80}),
            content_type='application/json',
        )
        self.assertIn(response.status_code, (302, 403))

    def test_unauthenticated_cannot_access_curation_data(self):
        """Unauthenticated request to curation_data gets redirected to login."""
        self.client.logout()
        response = self.client.get(self.curation_data_url)
        self.assertIn(response.status_code, (302, 403))

    def test_submitting_artist_cannot_access_curation_data(self):
        """A regular artist who submitted cannot access the curation_data endpoint."""
        self.client.force_login(self.artist1_user)
        response = self.client.get(self.curation_data_url)
        self.assertEqual(response.status_code, 404)

    # =========================================================================
    # Blind review
    # =========================================================================

    def test_blind_review_hides_artist_names_in_curation_data(self):
        """With blind_review enabled the artists list in curation_data is empty."""
        self.show.blind_review = True
        self.show.save(update_fields=['blind_review'])

        self.client.force_login(self.curator_user)
        data = self.client.get(self.curation_data_url).json()
        for artwork in data['artworks']:
            self.assertEqual(artwork['artists'], [],
                             f'Expected empty artists for {artwork["name"]} in blind review')
