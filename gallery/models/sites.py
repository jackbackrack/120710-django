from django.db import models
from django.urls import reverse
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit, Transpose

from gallery.models.slugs import build_unique_slug


class Site(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
    ]
    PUBLIC_STATUSES = {STATUS_PUBLISHED}

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    street = models.CharField(max_length=255, blank=True, verbose_name='Street address')
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True, verbose_name='State / Province / Region')
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True, default='USA')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    instagram = models.CharField(max_length=100, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='site_images', blank=True, null=True)
    card_sm = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=200)], format='JPEG', options={'quality': 80})
    card_md = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=600)], format='JPEG', options={'quality': 80})
    detail_lg = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=1200)], format='JPEG', options={'quality': 85})
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    @property
    def formatted_address(self):
        city_line = ', '.join(filter(None, [self.city, self.state]))
        if self.postal_code:
            city_line = f'{city_line} {self.postal_code}' if city_line else self.postal_code
        lines = [l for l in [self.street, city_line, self.country] if l]
        return '\n'.join(lines)

    def save(self, *args, **kwargs):
        self.slug = build_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('gallery:site_detail', kwargs={'slug': self.slug})
