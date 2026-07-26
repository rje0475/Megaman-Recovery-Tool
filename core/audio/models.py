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
        from core.settings import SettingsManager, get_settings_manager
        manager = (get_settings_manager() if environment is None else
                   SettingsManager(path=Path(os.devnull), environment=environment, create=False))
        values = manager.section("audio")
        ffmpeg = values["ffmpeg_path"] or which("ffmpeg")
        ffprobe = values["ffprobe_path"] or which("ffprobe")
        if not ffmpeg:
            try:
                from scanner import FFMPEG
                ffmpeg = FFMPEG if Path(FFMPEG).is_file() else None
            except ImportError:
                pass
        if not ffprobe and ffmpeg:
            sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            ffprobe = sibling if sibling.is_file() else None
        keep = values["keep_source"]
        return cls(
            ffmpeg_path=Path(ffmpeg) if ffmpeg else None,
            ffprobe_path=Path(ffprobe) if ffprobe else None,
            output_audio_format=values["output_format"],
            mp3_bitrate_kbps=int(values["bitrate_kbps"]),
            processing_timeout_seconds=int(values["processing_timeout_seconds"]),
            keep_source_after_processing=(keep if isinstance(keep, bool)
                                          else str(keep).casefold() not in {"0", "false", "no"}),
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
