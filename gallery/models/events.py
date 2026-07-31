from django.db import models
from django.urls import reverse

from gallery.models.exhibitions import Show
from gallery.models.slugs import build_unique_slug


class Event(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField(blank=True, null=True)
    show = models.ForeignKey(Show, related_name='events', on_delete=models.CASCADE)
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

    def google_calendar_url(self):
        """One-click "add this to my calendar". Absolute instants — see gallery/calendars.py."""
        from gallery.calendars import event_google_url
        return event_google_url(self)

    @property
    def time_range(self):
        """When it runs, both ends: "4:00–8:00 PM", or "11:00 AM–2:00 PM" across noon.

        Shared with opening hours rather than written twice — a campaign subject, the Visit page
        and an events list should not be able to format the same span three different ways. A
        property and not a template filter because a subject needs it, and subjects allow
        `{{ }}` only.
        """
        from gallery import timeranges
        return timeranges.time_range(self.start, self.end)
