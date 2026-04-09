from django.db import models
from django.urls import reverse

from gallery.models.exhibitions import Show
from gallery.models.people import Artist
from gallery.models.slugs import build_unique_slug


class Artwork(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    shows = models.ManyToManyField(Show, related_name='artworks', blank=True)
    artists = models.ManyToManyField(Artist, related_name='artworks')
    created_by = models.ForeignKey(
        'auth.User',
        related_name='created_artworks',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    end_year = models.IntegerField()
    start_year = models.IntegerField(verbose_name='Start_year: only fill in if different than end_year', blank=True, null=True)
    medium = models.TextField(blank=True, null=True)
    dimensions = models.CharField(verbose_name='Dimensions: LxWxD in inches', max_length=255, blank=True, null=True)
    image = models.ImageField(upload_to='artwork_images', blank=True, null=True)
    price = models.FloatField(verbose_name='Price: numeric price', blank=True, null=True)
    pricing = models.CharField(verbose_name='Pricing: anything more sophisticated like "Upon request" or "NFS"', max_length=255, blank=True, null=True)
    replacement_cost = models.FloatField(verbose_name='Replacment Cost: redo cost in the rare case that it gets stolen or damaged', blank=True, null=True)
    is_sold = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False)
    open_call_available = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    installation = models.TextField(verbose_name='Installation: optional instructions for installing your work', blank=True, null=True)
    tags = models.ManyToManyField('gallery.Tag', related_name='artworks', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        self.name = (self.name or '').strip()
        self.slug = build_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('gallery:artwork_detail', kwargs={'slug': self.slug})
