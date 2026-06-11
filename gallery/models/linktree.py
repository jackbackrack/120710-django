from django.db import models


class LinkTreeEntry(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField(max_length=500)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Link tree entry'
        verbose_name_plural = 'Link tree entries'

    def __str__(self):
        return self.name
