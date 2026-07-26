"""Defensieve ffprobe JSON-laag."""

import json
import subprocess
from pathlib import Path

from core.audio.errors import FfprobeNotFoundError, ProbeFailedError, beperk_stderr
from core.audio.models import AudioProbeResult


def _number(value, converter):
    try:
        return converter(value)
    except (TypeError, ValueError, OverflowError):
        return None


class AudioProbe:
    def __init__(self, executable, runner=subprocess.run, timeout=120):
        self.executable = Path(executable) if executable else None
        self.runner = runner
        self.timeout = timeout

    def inspect(self, path):
        if not self.executable or not self.executable.is_file():
            raise FfprobeNotFoundError("ffprobe is niet gevonden.")
        command = [
            str(self.executable), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
        try:
            result = self.runner(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout, check=False,
            )
        except FileNotFoundError as error:
            raise FfprobeNotFoundError("ffprobe is niet gevonden.") from error
        except (subprocess.TimeoutExpired, OSError) as error:
            raise ProbeFailedError(f"ffprobe mislukt: {error}") from error
        if result.returncode != 0:
            raise ProbeFailedError(
                "ffprobe kon het audiobestand niet inspecteren.", result.stderr
            )
        try:
            return self.parse(result.stdout)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ProbeFailedError("ffprobe gaf ongeldige JSON terug.") from error

    @staticmethod
    def parse(value):
        data = json.loads(value) if isinstance(value, str) else value
        if not isinstance(data, dict):
            raise ValueError("ffprobe-resultaat is geen object")
        streams = data.get("streams") if isinstance(data.get("streams"), list) else []
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        video = [s for s in streams if s.get("codec_type") == "video"]
        first = audio[0] if audio else {}
        fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
        return AudioProbeResult(
            format_name=fmt.get("format_name"),
            duration=_number(fmt.get("duration") or first.get("duration"), float),
            size=_number(fmt.get("size"), int),
            audio_codec=first.get("codec_name"),
            bit_rate=_number(first.get("bit_rate") or fmt.get("bit_rate"), int),
            sample_rate=_number(first.get("sample_rate"), int),
            channels=_number(first.get("channels"), int),
            audio_stream_count=len(audio), video_stream_count=len(video), raw=data,
        )
