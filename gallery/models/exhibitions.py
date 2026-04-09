import datetime

from django.conf import settings
from django.db import models
from django.urls import reverse

from gallery.models.people import Artist
from gallery.models.slugs import build_unique_slug


class Show(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='show_images', blank=True, null=True)
    artists = models.ManyToManyField(Artist, related_name='shows', blank=True)
    curators = models.ManyToManyField(Artist, blank=True)
    managing_curator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='managed_shows',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    is_open_call = models.BooleanField(default=False)
    start = models.DateField(default=datetime.date.today)
    end = models.DateField(default=datetime.date.today)
    tags = models.ManyToManyField('gallery.Tag', related_name='shows', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start']

    def save(self, *args, **kwargs):
        self.name = (self.name or '').strip()
        self.slug = build_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('gallery:show_detail', kwargs={'slug': self.slug})

    def get_placards_url(self):
        return reverse('gallery:show_placards_detail', kwargs={'slug': self.slug})

    def get_instagram_url(self):
        return reverse('gallery:show_instagram_detail', kwargs={'slug': self.slug})

    @property
    def curator_artist(self):
        if self.managing_curator_id:
            return self.managing_curator.artists.order_by('-created_at').first()
        return self.curators.order_by('-created_at').first()
