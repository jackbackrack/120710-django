from django.db import models

from gallery.models.artworks import Artwork
from gallery.models.exhibitions import Show


class ShowArtworkNumber(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='artwork_numbers')
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='show_numbers')
    number = models.PositiveIntegerField()

    class Meta:
        unique_together = [('show', 'number'), ('show', 'artwork')]
        ordering = ['number']

    def __str__(self):
        return f'{self.show.name} #{self.number}: {self.artwork.name}'
