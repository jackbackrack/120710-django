import datetime

from django.db import models
from django.urls import reverse
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit

from gallery.models.people import Artist
from gallery.models.slugs import build_unique_slug


class Show(models.Model):
    SHOW_TYPE_GALLERY = 'gallery'
    SHOW_TYPE_PUBLIC_ART = 'public_art'
    SHOW_TYPE_CHOICES = [
        (SHOW_TYPE_GALLERY, 'Gallery Show'),
        (SHOW_TYPE_PUBLIC_ART, 'Public Art Site'),
    ]

    STATUS_UNDER_CONSIDERATION = 'under_consideration'
    STATUS_OPEN_CALL = 'open_call'
    STATUS_IN_REVIEW = 'in_review'
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_UNDER_CONSIDERATION, 'Under Consideration'),
        (STATUS_OPEN_CALL, 'Open Call'),
        (STATUS_IN_REVIEW, 'In Review'),
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
        (STATUS_CLOSED, 'Closed'),
    ]
    PUBLIC_STATUSES = {STATUS_OPEN_CALL, STATUS_IN_REVIEW, STATUS_PUBLISHED, STATUS_CLOSED}

    VALID_TRANSITIONS = {
        STATUS_UNDER_CONSIDERATION: [STATUS_OPEN_CALL, STATUS_DRAFT],
        STATUS_OPEN_CALL: [STATUS_IN_REVIEW],
        STATUS_IN_REVIEW: [STATUS_DRAFT],
        STATUS_DRAFT: [STATUS_PUBLISHED],
        STATUS_PUBLISHED: [STATUS_CLOSED],
        STATUS_CLOSED: [],
    }

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    show_type = models.CharField(max_length=32, choices=SHOW_TYPE_CHOICES, default=SHOW_TYPE_GALLERY)
    location = models.TextField(blank=True, null=True, verbose_name='Location (address or site description)')
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='show_images', blank=True, null=True)
    card_thumbnail = ImageSpecField(source='image', processors=[ResizeToFit(width=600)], format='JPEG', options={'quality': 80})
    artists = models.ManyToManyField(Artist, related_name='shows', blank=True)
    curators = models.ManyToManyField(Artist, blank=True, related_name='curated_shows')
    is_open_call = models.BooleanField(default=False)
    submission_deadline = models.DateField(blank=True, null=True)
    review_deadline = models.DateField(blank=True, null=True, verbose_name='Review deadline (for jurors)')
    decision_date = models.DateField(blank=True, null=True)
    start = models.DateField(default=datetime.date.today)
    end = models.DateField(default=datetime.date.today)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_UNDER_CONSIDERATION, db_index=True)
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
    def date_range(self):
        # Example: "Jan 1, 2026 - Jan 5, 2026"
        if self.start.year == self.end.year :
          if self.start.month == self.end.month :
            if self.start.day == self.end.day :
              date = self.end.strftime("%b %d, %Y")
              return date
            else :
              start = self.start.strftime("%b %d")
              end = self.end.strftime("%d, %Y")
          else :
            start = self.start.strftime("%b %d")
            end = self.end.strftime("%b %d, %Y")
        else :
          start = self.start.strftime("%b %d, %Y")
          end = self.end.strftime("%b %d, %Y")
        return f"{start} – {end}"

    def transition_to(self, new_status):
        allowed = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f'Cannot transition from {self.status!r} to {new_status!r}. Allowed: {allowed}'
            )
        self.status = new_status
        self.save()

    @property
    def is_accepting_submissions(self):
        return self.status == self.STATUS_OPEN_CALL

    @property
    def open_call_phase(self):
        if self.status == self.STATUS_OPEN_CALL:
            return 'open'
        if self.status == self.STATUS_IN_REVIEW:
            return 'jury'
        return None

    @property
    def curator_artist(self):
        return self.curators.order_by('-created_at').first()
