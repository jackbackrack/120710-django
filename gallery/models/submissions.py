from django.conf import settings
from django.db import models

from gallery.models.artworks import Artwork
from gallery.models.exhibitions import Show


class ArtworkSubmission(models.Model):
    # Artist-visible status (updated when curator publishes decisions)
    SUBMITTED = 'submitted'
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'
    STATUS_CHOICES = [
        (SUBMITTED, 'Submitted'),
        (ACCEPTED, 'Accepted'),
        (REJECTED, 'Rejected'),
    ]

    # Curator-only draft decision (never shown to artists)
    UNDECIDED = 'undecided'
    CURATOR_SELECTED = 'selected'
    CURATOR_REJECTED = 'rejected'
    DECISION_CHOICES = [
        (UNDECIDED, 'Undecided'),
        (CURATOR_SELECTED, 'Selected'),
        (CURATOR_REJECTED, 'Rejected'),
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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=SUBMITTED)
    curator_decision = models.CharField(max_length=20, choices=DECISION_CHOICES, default=UNDECIDED)
    email_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('show', 'artwork')
        ordering = ['submitted_at']

    def __str__(self):
        return f'{self.artwork.name} → {self.show.name} ({self.status})'
