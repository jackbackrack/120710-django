from django.conf import settings
from django.db import models

from gallery.models.artworks import Artwork
from gallery.models.exhibitions import Show


class ArtworkSubmission(models.Model):
    SUBMITTED = 'submitted'
    SELECTED = 'selected'
    REJECTED = 'rejected'
    STATUS_CHOICES = [
        (SUBMITTED, 'Submitted'),
        (SELECTED, 'Selected'),
        (REJECTED, 'Rejected'),
    ]

    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='submissions')
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='submissions')
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='artwork_submissions',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    statement = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=SUBMITTED)

    class Meta:
        unique_together = ('show', 'artwork')
        ordering = ['submitted_at']

    def __str__(self):
        return f'{self.artwork.name} → {self.show.name} ({self.status})'
