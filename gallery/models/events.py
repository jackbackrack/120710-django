from django.conf import settings
from django.db import models
from django.urls import reverse

from gallery.models.exhibitions import Show
from gallery.models.slugs import build_unique_slug


class Event(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField(blank=True, null=True)
    show = models.ForeignKey(Show, related_name='events', on_delete=models.CASCADE)
    managing_curator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='managed_events',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    image = models.ImageField(upload_to='show_images', blank=True, null=True)
    date = models.DateField()
    start = models.TimeField()
    end = models.TimeField()
    tags = models.ManyToManyField('gallery.Tag', related_name='events', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def save(self, *args, **kwargs):
        self.name = (self.name or '').strip()
        self.slug = build_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.show.name + ' ' + self.name

    def get_absolute_url(self):
        return reverse('gallery:event_detail', kwargs={'slug': self.slug})
