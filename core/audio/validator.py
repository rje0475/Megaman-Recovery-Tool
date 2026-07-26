"""Technische MP3-validatie na conversie."""

from pathlib import Path

from core.audio.errors import (
    DurationMismatchError, EmptyOutputError, InvalidAudioError,
    VideoStreamPresentError, WrongCodecError, WrongFormatError,
)
from core.audio.models import AudioValidationResult


class AudioValidator:
    def validate(self, path, probe, source_duration=None):
        path = Path(path)
        if not path.is_file():
            raise InvalidAudioError("Verwerkt audiobestand ontbreekt.")
        if path.stat().st_size <= 0:
            raise EmptyOutputError("Verwerkt audiobestand is leeg.")
        try:
            with path.open("rb") as stream:
                if not stream.read(1):
                    raise EmptyOutputError("Verwerkt audiobestand is leeg.")
        except OSError as error:
            raise InvalidAudioError(f"Verwerkt audiobestand is niet leesbaar: {error}") from error
        formats = {part.strip().casefold() for part in (probe.format_name or "").split(",")}
        if not formats & {"mp3", "mp2", "mpeg"}:
            raise WrongFormatError("Uitvoercontainer is geen MP3.")
        if (probe.audio_codec or "").casefold() not in {"mp3", "mp3float"}:
            raise WrongCodecError("Uitvoercodec is geen MP3.")
        if probe.audio_stream_count < 1:
            raise InvalidAudioError("Uitvoer bevat geen audiostream.")
        if probe.video_stream_count:
            raise VideoStreamPresentError("Uitvoer bevat een videostream.")
        if not probe.duration or probe.duration <= 0:
            raise InvalidAudioError("Uitvoerduur ontbreekt of is ongeldig.")
        warnings = []
        if source_duration and source_duration > 0:
            tolerance = max(3.0, source_duration * .02)
            if abs(probe.duration - source_duration) > tolerance:
                raise DurationMismatchError(
                    "Uitvoerduur wijkt te sterk af van de bronaudio."
                )
        else:
            warnings.append("Bronduur onbekend; alleen positieve uitvoerduur gevalideerd.")
        return AudioValidationResult(True, tuple(warnings))
