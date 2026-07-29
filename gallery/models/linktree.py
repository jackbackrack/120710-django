from django.db import models


class LinkTreeEntry(models.Model):
    # Null means network-level: shown on every site's /links/ as well as the default
    # site's. A gallery's own links carry its site, so joining the network does not mean
    # inheriting another gallery's link list.
    site = models.ForeignKey(
        'gallery.Site', null=True, blank=True, on_delete=models.CASCADE,
        related_name='link_tree_entries',
        help_text='Leave blank for a link that should appear for every site.')
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
