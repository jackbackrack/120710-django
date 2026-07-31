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

    @staticmethod
    def _clock(when, meridiem=True):
        hour = when.hour % 12 or 12
        text = f'{hour}:{when.minute:02d}'
        return f'{text} {"AM" if when.hour < 12 else "PM"}' if meridiem else text

    @property
    def time_range(self):
        """When it runs, both ends: "4:00–8:00 PM", or "11:00 AM–2:00 PM" across noon.

        A property rather than a template filter because a campaign *subject* needs it too, and
        subjects allow `{{ }}` only — no `{% load %}` — so a filter could not reach them. One
        implementation then serves the subject, the body and the events list alike.

        The meridiem is dropped from the start when both ends share it, which is how the old
        Mailchimp announcements read and how anybody writes an opening time by hand.
        """
        same_half = (self.start.hour < 12) == (self.end.hour < 12)
        return f'{self._clock(self.start, not same_half)}–{self._clock(self.end)}'

    def __str__(self):
        return self.show.name + ' ' + self.name

    def get_absolute_url(self):
        return reverse('gallery:event_detail', kwargs={'slug': self.slug})

    @property
    def time_range(self):
        def fmt(t, include_ampm):
            s = t.strftime('%I:%M %p').lstrip('0')
            return s if include_ampm else s.rsplit(' ', 1)[0]
        start_ampm = self.start.strftime('%p')
        end_ampm = self.end.strftime('%p')
        if start_ampm == end_ampm:
            return f'{fmt(self.start, False)}–{fmt(self.end, True)}'
        return f'{fmt(self.start, True)}–{fmt(self.end, True)}'
