from django.apps import AppConfig


class GalleryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gallery'
    verbose_name = 'Gallery'

    def ready(self):
        import atexit

        from django.conf import settings

        missing_posthog_setting = next(
            (
                name
                for name in ('POSTHOG_PROJECT_TOKEN', 'POSTHOG_HOST')
                if not getattr(settings, name)
            ),
            None,
        )
        if missing_posthog_setting and settings.DEBUG:
            raise RuntimeError(
                f'{missing_posthog_setting} variable required by PostHog is missing or '
                f'un-configured, this causes events to be silently missed. This error stops '
                f'appearing once {missing_posthog_setting} is configured'
            )

        if not missing_posthog_setting:
            from posthog import Posthog

            self.posthog_client = Posthog(
                settings.POSTHOG_PROJECT_TOKEN,
                host=settings.POSTHOG_HOST,
                enable_exception_autocapture=True,
            )
            atexit.register(self.posthog_client.shutdown)

        import gallery.signals  # noqa
        # Connects the Resend bounce/complaint receiver. Importing here rather than at
        # module scope keeps it out of the app-loading path until Django is ready.
        import gallery.webhooks  # noqa
