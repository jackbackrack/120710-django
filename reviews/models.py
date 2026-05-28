from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from gallery.models.artworks import Artwork
from gallery.models.exhibitions import Show


class ShowJuror(models.Model):
    """Assigns a user as juror for a specific show."""
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='jurors')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='juror_assignments',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='juror_assignments_made',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('show', 'user')
        ordering = ['show', 'user__last_name', 'user__first_name']

    def __str__(self):
        name = self.user.get_full_name() or self.user.username if self.user else '(deleted user)'
        return f'{name} - {self.show.name}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)


class RubricCriterion(models.Model):
    """A weighted scoring criterion defined per show for jury evaluation."""
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='rubric_criteria')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    weight = models.FloatField(default=1.0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ('show', 'name')
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.show.name}: {self.name} (weight={self.weight})'


class ArtworkReview(models.Model):
    """A juror's rating and review of an artwork within the context of a specific show."""
    RATING_CHOICES = [(i, str(i)) for i in range(1, 11)]

    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='reviews')
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='reviews')
    juror = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='artwork_reviews',
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        null=True,
        blank=True,
    )
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('show', 'artwork', 'juror')
        ordering = ['artwork__name', 'juror__last_name']

    def __str__(self):
        name = self.juror.get_full_name() or self.juror.username if self.juror else '(deleted user)'
        return f'{name}: {self.artwork.name} ({self.show.name}) - {self.rating}/10'


class CriterionScore(models.Model):
    """A juror's score on one rubric criterion for one artwork review."""
    review = models.ForeignKey(ArtworkReview, on_delete=models.CASCADE, related_name='criterion_scores')
    criterion = models.ForeignKey(RubricCriterion, on_delete=models.CASCADE, related_name='scores')
    score = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )

    class Meta:
        unique_together = ('review', 'criterion')

    def __str__(self):
        return f'{self.criterion.name}={self.score} ({self.review})'
