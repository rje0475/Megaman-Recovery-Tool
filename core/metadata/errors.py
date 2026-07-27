"""Domeinfouten voor metadata en veilige finalisatie."""


class MetadataError(Exception):
    code = "METADATA_ERROR"


class MetadataMissingError(MetadataError):
    code = "METADATA_MISSING"


class ArtworkMissingError(MetadataError):
    code = "ARTWORK_MISSING"


class ArtworkDownloadError(MetadataError):
    code = "ARTWORK_DOWNLOAD_FAILED"


class MetadataWriteError(MetadataError):
    code = "METADATA_WRITE_FAILED"


class FinalizationValidationError(MetadataError):
    code = "FINALIZATION_INVALID"


class FinalizationSkipped(MetadataError):
    code = "FINALIZATION_SKIPPED"
