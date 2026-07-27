"""Begrijpelijke audioprocessingfouten met persistente codes."""


MAX_STDERR_LENGTH = 8000


def beperk_stderr(value, limit=MAX_STDERR_LENGTH):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n… [afgekapt]"


class AudioProcessingError(RuntimeError):
    code = "UNKNOWN_PROCESSING_ERROR"

    def __init__(self, message=None, stderr=None):
        super().__init__(message or self.code)
        self.stderr = beperk_stderr(stderr)


def _error(name, code):
    return type(name, (AudioProcessingError,), {"code": code})


FfmpegNotFoundError = _error("FfmpegNotFoundError", "FFMPEG_NOT_FOUND")
FfprobeNotFoundError = _error("FfprobeNotFoundError", "FFPROBE_NOT_FOUND")
FfmpegFailedError = _error("FfmpegFailedError", "FFMPEG_FAILED")
FfmpegTimeoutError = _error("FfmpegTimeoutError", "FFMPEG_TIMEOUT")
FfmpegCancelledError = _error("FfmpegCancelledError", "FFMPEG_CANCELLED")
ProbeFailedError = _error("ProbeFailedError", "PROBE_FAILED")
InvalidAudioError = _error("InvalidAudioError", "INVALID_AUDIO")
EmptyOutputError = _error("EmptyOutputError", "EMPTY_OUTPUT")
WrongFormatError = _error("WrongFormatError", "WRONG_FORMAT")
WrongCodecError = _error("WrongCodecError", "WRONG_CODEC")
VideoStreamPresentError = _error("VideoStreamPresentError", "VIDEO_STREAM_PRESENT")
DurationMismatchError = _error("DurationMismatchError", "DURATION_MISMATCH")
DiskFullProcessingError = _error("DiskFullProcessingError", "DISK_FULL")
PermissionProcessingError = _error("PermissionProcessingError", "PERMISSION_DENIED")
SourceMissingError = _error("SourceMissingError", "SOURCE_MISSING")


def classify_processing_os_error(error):
    if getattr(error, "errno", None) == 28:
        return DiskFullProcessingError("Onvoldoende schijfruimte.")
    if isinstance(error, PermissionError) or getattr(error, "errno", None) in {1, 13}:
        return PermissionProcessingError("Geen toegang tot de verwerkingsmap.")
    return AudioProcessingError(str(error) or "Onbekende verwerkingsfout.")
