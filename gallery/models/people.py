import os
import re

from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django_countries.fields import CountryField
from imagekit.models import ImageSpecField

from gallery.imaging import web_processors

from gallery.models.slugs import build_unique_slug


def _sanitize_upload_filename(directory, filename):
    name, ext = os.path.splitext(filename)
    name = re.sub(r'[^A-Za-z0-9_\-]', '-', name)
    name = re.sub(r'-{2,}', '-', name).strip('-') or 'image'
    return os.path.join(directory, name + ext)


def artist_image_upload(instance, filename):
    return _sanitize_upload_filename('artist_images', filename)


class Artist(models.Model):
    user = models.ForeignKey(User, related_name='artists', on_delete=models.SET_NULL, blank=True, null=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    first_name = models.CharField(max_length=255, blank=True, default='')
    last_name = models.CharField(max_length=255, blank=True, default='')
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=255, blank=True, default='')
    # Postal address. Only the country and the postal code are relied on: they are what
    # decides whether a submission is inside a site's area. Street and city are for the
    # gallery's own records — shipping, correspondence — and are never shown publicly;
    # see `can_see_contact` on the artist detail page.
    street = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=255, blank=True, default='')
    state = models.CharField(verbose_name='State / province', max_length=255,
                             blank=True, default='')
    # ISO 3166-1 alpha-2, not free text, and matching Site.country so a national show can
    # compare the two directly. Free text is exactly the ambiguity to avoid here:
    # "US"/"USA"/"United States" would all appear and the in-area test has to be reliable.
    # Defaulted rather than left blank so every artist has a usable value without a new
    # gate in the submission flow.
    country = CountryField(default='US')
    zipcode = models.CharField(verbose_name='ZIP / postal code',
                               max_length=10, blank=True, default='')
    website = models.URLField(max_length=255, blank=True, null=True)
    instagram = models.CharField(verbose_name='Instagram: your handle starting with @', max_length=255, blank=True, null=True)
    venmo = models.CharField(verbose_name='Venmo: your username starting with @', max_length=255, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    statement = models.TextField(blank=True, null=True)
    image = models.ImageField(
        upload_to=artist_image_upload, blank=True, null=True,
        verbose_name='Profile photo',
    )
    card_sm = ImageSpecField(source='image', processors=web_processors(width=200), format='JPEG', options={'quality': 80})
    card_md = ImageSpecField(source='image', processors=web_processors(width=600), format='JPEG', options={'quality': 80})
    detail_lg = ImageSpecField(source='image', processors=web_processors(width=1200), format='JPEG', options={'quality': 85})
    slideshow = ImageSpecField(source='image', processors=web_processors(width=1920), format='JPEG', options={'quality': 85})
    tags = models.ManyToManyField('gallery.Tag', related_name='artists', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['-created_at'])]

    @property
    def full_name(self):
        full_name = ' '.join(part for part in [self.first_name, self.last_name] if part).strip()
        return full_name or self.name

    def save(self, *args, **kwargs):
        self.name = (self.name or '').strip()
        self.first_name = (self.first_name or '').strip()
        self.last_name = (self.last_name or '').strip()

        if (not self.first_name and not self.last_name) and self.name:
            parts = self.name.split(None, 1)
            self.first_name = parts[0]
            self.last_name = parts[1] if len(parts) > 1 else ''

        if self.first_name or self.last_name:
            self.name = ' '.join(part for part in [self.first_name, self.last_name] if part).strip()

        if self.instagram:
            self.instagram = self.instagram.strip()
            if not self.instagram.startswith('@'):
                self.instagram = '@' + self.instagram

        if self.venmo:
            self.venmo = self.venmo.strip()
            if not self.venmo.startswith('@'):
                self.venmo = '@' + self.venmo

        if self.website:
            self.website = self.website.strip()
            if self.website and '://' not in self.website:
                self.website = 'https://' + self.website

        self.slug = build_unique_slug(self, self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse('gallery:artist_detail', kwargs={'slug': self.slug})

    @property
    def is_curator(self):
        return self.curated_shows.exists()
