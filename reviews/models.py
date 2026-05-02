from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.roles import add_juror_role
from gallery.models.artworks import Artwork
from gallery.models.exhibitions import Show


class ShowJuror(models.Model):
    """Assigns a user as juror for a specific show."""
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='jurors')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
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
        return f'{self.user.get_full_name() or self.user.username} - {self.show.name}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        add_juror_role(self.user)


class ArtworkReview(models.Model):
    """A juror's rating and review of an artwork within the context of a specific show."""
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='reviews')
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='reviews')
    juror = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='artwork_reviews',
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('show', 'artwork', 'juror')
        ordering = ['artwork__name', 'juror__last_name']

    def __str__(self):
        return f'{self.juror.get_full_name() or self.juror.username}: {self.artwork.name} ({self.show.name}) - {self.rating}/5'
