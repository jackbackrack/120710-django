from imagekit.cachefiles.strategies import JustInTime as _JustInTime


class JustInTime(_JustInTime):
    """
    JustInTime strategy that resets the source file handle before generating.

    When multiple ImageSpecFields share the same source (e.g. card_md and
    detail_lg both sourced from `image`), the first generation closes the
    source FieldFile, leaving _file set to a closed handle.  Django's
    FieldFile.open() will try to re-open that handle rather than re-fetching
    from storage, which raises "The file cannot be reopened." for S3 files.

    Resetting _file to None forces the next open() to fetch fresh from storage.
    """
    def on_existence_required(self, file):
        source = getattr(file.generator, 'source', None)
        if source is not None and getattr(source, '_file', None) is not None:
            source._file = None
        super().on_existence_required(file)
