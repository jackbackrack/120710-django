from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse

from gallery.models.slugs import build_unique_slug


class Artist(models.Model):
    user = models.ForeignKey(User, related_name='artists', on_delete=models.CASCADE, blank=True, null=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    first_name = models.CharField(max_length=255, blank=True, default='')
    last_name = models.CharField(max_length=255, blank=True, default='')
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=255)
    website = models.URLField(max_length=255, blank=True, null=True)
    instagram = models.CharField(verbose_name='Instagram: your handle starting with @', max_length=255, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    statement = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='artist_images', null=True)
    tags = models.ManyToManyField('gallery.Tag', related_name='artists', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

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

        self.slug = build_unique_slug(self, self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse('gallery:artist_detail', kwargs={'slug': self.slug})

    @property
    def is_curator(self):
        return bool(self.user and self.user.groups.filter(name='curator').exists())
