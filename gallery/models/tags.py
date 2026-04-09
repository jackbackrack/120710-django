from django.conf import settings
from django.db import models
from django.utils.text import slugify


OPEN_CALL_TAG_NAME = 'Open Call'
OPEN_CALL_TAG_SLUG = 'open-call'


class Tag(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    curators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='curator_tags',
        blank=True,
    )

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


def ensure_open_call_tag():
    tag, created = Tag.objects.get_or_create(
        slug=OPEN_CALL_TAG_SLUG,
        defaults={'name': OPEN_CALL_TAG_NAME},
    )
    if not created and tag.name != OPEN_CALL_TAG_NAME:
        tag.name = OPEN_CALL_TAG_NAME
        tag.save(update_fields=['name'])
    return tag
