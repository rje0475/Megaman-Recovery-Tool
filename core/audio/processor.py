"""FFmpeg-gebaseerde, annuleerbare MP3-processingpipeline."""

import subprocess
import time
from pathlib import Path
from threading import Event, Thread

from core.audio.errors import (
    AudioProcessingError, FfmpegCancelledError, FfmpegFailedError,
    FfmpegNotFoundError, FfmpegTimeoutError, SourceMissingError,
    classify_processing_os_error,
)
from core.audio.models import (
    AudioProcessingProgress, AudioProcessingResult, PreparedAudioProcessing,
)


class AudioProcessor:
    def __init__(self, config, probe, validator, popen=subprocess.Popen):
        self.config = config
        self.probe = probe
        self.validator = validator
        self.popen = popen
        self._cancelled = Event()
        self._process = None

    def prepare(self, job, processed_root):
        source = Path(job.download_path or "")
        if not source.is_file():
            raise SourceMissingError("Gedownloade bronaudio ontbreekt.")
        workspace = Path(processed_root) / job.job_id
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise classify_processing_os_error(error) from error
        self._cancelled.clear()
        prepared = PreparedAudioProcessing(
            job.job_id, source, workspace,
            workspace / "processing.tmp.mp3",
            workspace / "processed_audio.mp3",
        )
        self.cleanup_partial_output(prepared)
        return prepared

    def build_command(self, prepared):
        if not self.config.ffmpeg_path or not Path(self.config.ffmpeg_path).is_file():
            raise FfmpegNotFoundError("FFmpeg is niet gevonden.")
        return [
            str(self.config.ffmpeg_path), "-hide_banner", "-nostdin", "-y",
            "-i", str(prepared.source_path), "-map", "0:a:0", "-vn", "-sn",
            "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
            "-c:a", "libmp3lame", "-b:a",
            f"{self.config.mp3_bitrate_kbps}k", "-progress", "pipe:1",
            "-nostats", "-f", self.config.output_audio_format,
            str(prepared.temporary_path),
        ]

    def process(self, prepared, progress_callback=None, stage_callback=None):
        started = time.monotonic()
        command = self.build_command(prepared)
        source_probe = self.probe.inspect(prepared.source_path)
        if stage_callback:
            stage_callback("PROCESSING")
        try:
            self._process = self.popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            stderr_parts = []
            stderr_thread = Thread(
                target=lambda: stderr_parts.append(self._process.stderr.read()),
                daemon=True,
            )
            stderr_thread.start()
            deadline = time.monotonic() + self.config.processing_timeout_seconds
            while self._process.poll() is None:
                if self._cancelled.is_set():
                    self._terminate()
                    raise FfmpegCancelledError("Audioverwerking geannuleerd.")
                if time.monotonic() > deadline:
                    self._terminate()
                    raise FfmpegTimeoutError("FFmpeg-verwerking duurde te lang.")
                line = self._process.stdout.readline()
                if line.startswith("out_time_ms="):
                    try:
                        seconds = int(line.partition("=")[2]) / 1_000_000
                    except ValueError:
                        seconds = 0
                    percent = (
                        min(100.0, seconds * 100 / source_probe.duration)
                        if source_probe.duration else None
                    )
                    if progress_callback:
                        progress_callback(AudioProcessingProgress(
                            percent, "PROCESSING", "Audio converteren"
                        ))
                elif not line:
                    time.sleep(.02)
            stderr_thread.join(timeout=1)
            if self._cancelled.is_set():
                raise FfmpegCancelledError("Audioverwerking geannuleerd.")
            if self._process.returncode != 0:
                raise FfmpegFailedError(
                    f"FFmpeg stopte met exitcode {self._process.returncode}.",
                    "".join(stderr_parts),
                )
            if stage_callback:
                stage_callback("VALIDATING")
            output_probe = self.probe.inspect(prepared.temporary_path)
            validation = self.validator.validate(
                prepared.temporary_path, output_probe, source_probe.duration
            )
            prepared.temporary_path.replace(prepared.final_path)
            return AudioProcessingResult(
                processed_path=prepared.final_path,
                format=self.config.output_audio_format,
                size=prepared.final_path.stat().st_size,
                duration=output_probe.duration,
                bitrate=output_probe.bit_rate,
                sample_rate=output_probe.sample_rate,
                channels=output_probe.channels,
                codec=output_probe.audio_codec,
                validation=validation,
                source_duration=source_probe.duration,
                source_format=source_probe.format_name,
                processing_duration=time.monotonic() - started,
            )
        except AudioProcessingError:
            self.cleanup_partial_output(prepared)
            raise
        except OSError as error:
            self.cleanup_partial_output(prepared)
            raise classify_processing_os_error(error) from error
        finally:
            self._process = None

    def verify_output(self, prepared, source_duration=None):
        probe = self.probe.inspect(prepared.final_path)
        return self.validator.validate(prepared.final_path, probe, source_duration)

    def recognize_existing(self, prepared, source_duration=None):
        started = time.monotonic()
        output_probe = self.probe.inspect(prepared.final_path)
        validation = self.validator.validate(
            prepared.final_path, output_probe, source_duration
        )
        return AudioProcessingResult(
            processed_path=prepared.final_path,
            format=self.config.output_audio_format,
            size=prepared.final_path.stat().st_size,
            duration=output_probe.duration,
            bitrate=output_probe.bit_rate,
            sample_rate=output_probe.sample_rate,
            channels=output_probe.channels,
            codec=output_probe.audio_codec,
            validation=validation,
            source_duration=source_duration,
            source_format=None,
            processing_duration=time.monotonic() - started,
        )

    def cancel(self):
        self._cancelled.set()
        if self._process and self._process.poll() is None:
            self._terminate()

    @staticmethod
    def cleanup_partial_output(prepared):
        try:
            prepared.temporary_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _terminate(self):
        if not self._process or self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=3)
