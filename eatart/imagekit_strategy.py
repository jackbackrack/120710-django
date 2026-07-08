from imagekit.cachefiles.strategies import Optimistic as _Optimistic


class Optimistic(_Optimistic):
    """
    Optimistic strategy that skips S3 existence checks on .url access.

    JustInTime called storage.exists() (an S3 HEAD request) on every .url
    access unless the result was in Django's local cache. With hundreds of
    images per page that becomes thousands of S3 requests and causes timeouts.
    Optimistic assumes thumbnails already exist (they are generated on image
    save via on_source_saved) and returns URLs without any network call.

    The source file-handle reset from the old JustInTime subclass is preserved
    here on on_source_saved: when multiple ImageSpecFields share the same
    source, the first generation closes the FieldFile handle. Resetting _file
    to None forces the next open() to fetch fresh from S3.
    """
    def on_source_saved(self, file):
        source = getattr(file.generator, 'source', None)
        if source is not None and getattr(source, '_file', None) is not None:
            source._file = None
        super().on_source_saved(file)
