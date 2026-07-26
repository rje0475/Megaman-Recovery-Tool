"""Configuratie- en resultaatmodellen voor audioverwerking."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioProcessingConfig:
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None
    output_audio_format: str = "mp3"
    mp3_bitrate_kbps: int = 320
    processing_timeout_seconds: int = 1800
    keep_source_after_processing: bool = True

    @classmethod
    def from_environment(cls, environment=None, which=shutil.which):
        environment = os.environ if environment is None else environment
        ffmpeg = environment.get("FFMPEG_PATH") or which("ffmpeg")
        ffprobe = environment.get("FFPROBE_PATH") or which("ffprobe")
        if not ffmpeg:
            try:
                from scanner import FFMPEG
                ffmpeg = FFMPEG if Path(FFMPEG).is_file() else None
            except ImportError:
                pass
        if not ffprobe and ffmpeg:
            sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            ffprobe = sibling if sibling.is_file() else None
        keep = str(environment.get("KEEP_SOURCE_AFTER_PROCESSING", "true")).casefold()
        return cls(
            ffmpeg_path=Path(ffmpeg) if ffmpeg else None,
            ffprobe_path=Path(ffprobe) if ffprobe else None,
            output_audio_format=environment.get("OUTPUT_AUDIO_FORMAT", "mp3"),
            mp3_bitrate_kbps=int(environment.get("MP3_BITRATE_KBPS", "320")),
            processing_timeout_seconds=int(
                environment.get("PROCESSING_TIMEOUT_SECONDS", "1800")
            ),
            keep_source_after_processing=keep not in {"0", "false", "no"},
        )


@dataclass(frozen=True)
class AudioProbeResult:
    format_name: str | None
    duration: float | None
    size: int | None
    audio_codec: str | None
    bit_rate: int | None
    sample_rate: int | None
    channels: int | None
    audio_stream_count: int
    video_stream_count: int
    raw: dict


@dataclass(frozen=True)
class AudioValidationResult:
    valid: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AudioProcessingProgress:
    percent: float | None
    stage: str
    message: str


@dataclass(frozen=True)
class PreparedAudioProcessing:
    job_id: str
    source_path: Path
    workspace: Path
    temporary_path: Path
    final_path: Path


@dataclass(frozen=True)
class AudioProcessingResult:
    processed_path: Path
    format: str
    size: int
    duration: float
    bitrate: int | None
    sample_rate: int | None
    channels: int | None
    codec: str
    validation: AudioValidationResult
    source_duration: float | None
    source_format: str | None
    processing_duration: float
