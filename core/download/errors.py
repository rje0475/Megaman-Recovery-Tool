"""Gestandaardiseerde downloadfouten voor opslag en GUI."""


class DownloadError(RuntimeError):
    code = "UNKNOWN"

    def __init__(self, message=None, *, code=None):
        super().__init__(message or self.code)
        self.code = code or self.code


class DownloadCancelled(DownloadError):
    code = "CANCELLED"


class VideoRemovedError(DownloadError):
    code = "VIDEO_REMOVED"


class PrivateVideoError(DownloadError):
    code = "VIDEO_PRIVATE"


class GeoBlockedError(DownloadError):
    code = "GEO_BLOCKED"


class NetworkDownloadError(DownloadError):
    code = "NETWORK"


class DiskFullError(DownloadError):
    code = "DISK_FULL"


class DownloadTimeoutError(DownloadError):
    code = "TIMEOUT"


class DownloadInterrupted(DownloadError):
    code = "INTERRUPTED"


class ProviderDownloadError(DownloadError):
    code = "YTDLP_ERROR"


class DownloadVerificationError(DownloadError):
    code = "VERIFY_FAILED"


def classify_download_error(error):
    if isinstance(error, DownloadError):
        return error
    if isinstance(error, KeyboardInterrupt):
        return DownloadInterrupted("Download onderbroken.")
    if isinstance(error, TimeoutError):
        return DownloadTimeoutError("Download-timeout.")
    if isinstance(error, OSError):
        if getattr(error, "errno", None) == 28:
            return DiskFullError("Onvoldoende schijfruimte.")
        return NetworkDownloadError(str(error) or "Netwerk- of I/O-fout.")
    text = str(error or "").casefold()
    if "private video" in text or "video is private" in text:
        return PrivateVideoError(str(error))
    if "not available in your country" in text or "geo" in text and "block" in text:
        return GeoBlockedError(str(error))
    if "removed" in text or "unavailable" in text:
        return VideoRemovedError(str(error))
    if "timed out" in text or "timeout" in text:
        return DownloadTimeoutError(str(error))
    if "network" in text or "http error" in text or "connection" in text:
        return NetworkDownloadError(str(error))
    return ProviderDownloadError(str(error) or "Onbekende yt-dlp-fout.")
