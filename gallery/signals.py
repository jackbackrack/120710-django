from django.db.models.signals import post_save
from django.dispatch import receiver


def _generate_thumbnail(instance):
    if instance.image:
        try:
            instance.card_thumbnail.generate()
        except Exception:
            pass


@receiver(post_save, sender='gallery.Artist')
def generate_artist_thumbnail(sender, instance, **kwargs):
    _generate_thumbnail(instance)


@receiver(post_save, sender='gallery.Artwork')
def generate_artwork_thumbnail(sender, instance, **kwargs):
    _generate_thumbnail(instance)


@receiver(post_save, sender='gallery.Show')
def generate_show_thumbnail(sender, instance, **kwargs):
    _generate_thumbnail(instance)
