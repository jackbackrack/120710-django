from django.contrib.auth.models import User
from django.db import models


class SavedArtwork(models.Model):
    user = models.ForeignKey(User, related_name='saved_artworks', on_delete=models.CASCADE)
    artwork = models.ForeignKey('gallery.Artwork', related_name='saved_by', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'artwork')]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} saved {self.artwork}'


class CollectionPiece(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_DECLINED = 'declined'
    STATUS_CHOICES = [
        ('pending', 'Pending confirmation'),
        ('confirmed', 'Confirmed'),
        ('declined', 'Declined'),
    ]

    collector = models.ForeignKey(User, related_name='collection_pieces', on_delete=models.CASCADE)
    artwork = models.ForeignKey('gallery.Artwork', related_name='collection_pieces', on_delete=models.CASCADE)
    purchase_date = models.DateField(blank=True, null=True)
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    confirmed_by = models.ForeignKey(
        'gallery.Artist', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='confirmed_sales',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('collector', 'artwork')]
        ordering = ['display_order', '-created_at']

    def __str__(self):
        return f'{self.collector} owns {self.artwork} ({self.status})'
