from django.apps import apps
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver
from posthog import identify_context


@receiver(user_logged_in)
def identify_posthog_user(sender, request, user, **kwargs):
    """Identify the login request after Django establishes the authenticated user."""
    distinct_id = str(user.pk)
    identify_context(distinct_id)

    posthog_client = getattr(apps.get_app_config('gallery'), 'posthog_client', None)
    if posthog_client:
        posthog_client.set(
            distinct_id=distinct_id,
            properties={
                'email': user.email,
                'name': user.get_full_name(),
                'is_staff': user.is_staff,
            },
        )


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
