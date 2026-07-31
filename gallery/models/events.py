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

    @property
    def display_image(self):
        """The event's own picture, or the show's.

        An event without one is still an event *about* a show that has one, and a talk or a
        closing party rarely gets photographed in advance — so the choice is the show's image or
        a blank card, and the show's image is what the reader would expect to see anyway.

        Returns None rather than an empty file, because `.url` on an unset ImageField raises.
        """
        if self.image:
            return self.image
        show_image = getattr(self.show, 'image', None)
        return show_image or None

    # Below this a count discourages rather than encourages: "3 coming" reads as an empty room,
    # and early in a cycle it is always 3. Shown only once it argues for itself.
    RSVP_COUNT_THRESHOLD = 8

    def _rsvp_total(self, response):
        from django.db.models import Sum
        return self.rsvps.filter(response=response).aggregate(n=Sum('party_size'))['n'] or 0

    @property
    def rsvp_count(self):
        """People expected, counting guests — not the number of replies."""
        from gallery.models.rsvps import EventRsvp
        return self._rsvp_total(EventRsvp.YES)

    @property
    def rsvp_maybe_count(self):
        from gallery.models.rsvps import EventRsvp
        return self._rsvp_total(EventRsvp.MAYBE)

    @property
    def rsvp_count_public(self):
        """The count only when it flatters, otherwise None. See RSVP_COUNT_THRESHOLD."""
        count = self.rsvp_count
        return count if count >= self.RSVP_COUNT_THRESHOLD else None

    @property
    def is_past(self):
        from django.utils import timezone
        return self.date < timezone.now().date()

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
