from django.apps import AppConfig


class GalleryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gallery'
    verbose_name = 'Gallery'

    def ready(self):
        import gallery.signals  # noqa
        # Connects the Resend bounce/complaint receiver. Importing here rather than at
        # module scope keeps it out of the app-loading path until Django is ready.
        import gallery.webhooks  # noqa
