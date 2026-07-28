from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage

# class MediaStorage(S3Boto3Storage):
#     location = 'media'
#     file_overwrite = False


class HowtoImageStorage(S3Boto3Storage):
    """S3 storage for the generated how-to screenshots, on its own prefix.

    Only `manage.py capture_howto --publish` uses this; serving needs nothing but
    `settings.HOWTO_IMAGE_BASE_URL`, so no web server needs boto3 or write credentials.

    Configured from the environment rather than from the USE_S3_STATIC / USE_S3_MEDIA
    switches on purpose — publishing has to work from a local dev checkout (the only
    place a capture can run) without also redirecting the rest of local media into the
    bucket. See the HOWTO_IMAGE_* block in settings.py for why this prefix is separate
    from both `static/` and `media/`.

    Names are content-hashed by the caller, so an object is never rewritten with
    *different* bytes. That is what makes the bucket's `immutable, max-age=1y` cache
    headers safe here: a regenerated screenshot is a new key and a new URL, not a stale
    cache entry.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault('bucket_name', settings.HOWTO_IMAGE_BUCKET)
        kwargs.setdefault('location', settings.HOWTO_IMAGE_LOCATION)
        kwargs.setdefault('region_name', settings.HOWTO_IMAGE_REGION)
        kwargs.setdefault('default_acl', 'public-read')
        kwargs.setdefault('querystring_auth', False)
        # Set here rather than inherited: settings.AWS_S3_OBJECT_PARAMETERS only exists
        # inside the `if USE_S3_STATIC or USE_S3_MEDIA` block, which this storage is
        # deliberately independent of — so without this, published screenshots came back
        # with no Cache-Control at all, and content-hashing bought nothing.
        kwargs.setdefault('object_parameters', {
            'CacheControl': 'public, max-age=31536000, immutable',
        })
        # Re-publishing unchanged content targets the identical hashed key, and
        # overwriting it with byte-identical bytes is exactly what we want. Under the
        # project-wide file_overwrite=False it would instead pile up `06.abc_xY9.webp`
        # duplicates on every run.
        kwargs.setdefault('file_overwrite', True)
        super().__init__(**kwargs)
