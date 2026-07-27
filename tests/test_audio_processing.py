import io
import os
import tempfile
import unittest
import time
from threading import Thread
from pathlib import Path
from unittest.mock import Mock

from core.audio.errors import (
    DurationMismatchError, EmptyOutputError, FfmpegNotFoundError,
    FfmpegTimeoutError, FfprobeNotFoundError, VideoStreamPresentError,
    WrongCodecError, WrongFormatError, FfmpegCancelledError,
    FfmpegFailedError, beperk_stderr, classify_processing_os_error,
)
from core.audio.models import (
    AudioProbeResult, AudioProcessingConfig, PreparedAudioProcessing,
)
from core.audio.probe import AudioProbe
from core.audio.processor import AudioProcessor
from core.audio.validator import AudioValidator


def probe_result(**values):
    defaults = dict(
        format_name="mp3", duration=100.0, size=1000, audio_codec="mp3",
        bit_rate=320000, sample_rate=44100, channels=2,
        audio_stream_count=1, video_stream_count=0, raw={},
    )
    defaults.update(values)
    return AudioProbeResult(**defaults)


class SequenceProbe:
    def __init__(self, *results):
        self.results = list(results)
    def inspect(self, _path):
        return self.results.pop(0)


class FinishedProcess:
    def __init__(self, command, payload=b"mp3", returncode=0, **_kwargs):
        self.command = command
        Path(command[-1]).write_bytes(payload)
        self.stdout = io.StringIO("out_time_ms=50000000\nprogress=end\n")
        self.stderr = io.StringIO("technisch")
        self.returncode = returncode
    def poll(self): return self.returncode
    def terminate(self): self.returncode = -15
    def kill(self): self.returncode = -9
    def wait(self, timeout=None): return self.returncode


class HangingProcess(FinishedProcess):
    def __init__(self, command, **kwargs):
        super().__init__(command, **kwargs)
        self.returncode = None
        self.stdout = io.StringIO("")
    def poll(self): return self.returncode


class AudioProcessingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ffmpeg = self.root / "ffmpeg.exe"
        self.ffprobe = self.root / "ffprobe.exe"
        self.ffmpeg.touch(); self.ffprobe.touch()
        self.source = self.root / "source.webm"
        self.source.write_bytes(b"source")
        self.config = AudioProcessingConfig(
            self.ffmpeg, self.ffprobe, processing_timeout_seconds=10
        )

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self):
        workspace = self.root / "downloads" / "processed" / "job"
        workspace.mkdir(parents=True, exist_ok=True)
        return PreparedAudioProcessing(
            "job", self.source, workspace,
            workspace / "processing.tmp.mp3",
            workspace / "processed_audio.mp3",
        )

    def test_command_is_audio_only_320kbps_zonder_metadata(self):
        processor = AudioProcessor(
            self.config, SequenceProbe(), AudioValidator()
        )
        command = processor.build_command(self.prepared())
        self.assertIn("libmp3lame", command)
        self.assertIn("320k", command)
        self.assertIn("0:a:0", command)
        self.assertIn("-vn", command)
        self.assertIn("-sn", command)
        self.assertIn("-dn", command)
        self.assertEqual(command[command.index("-map_metadata") + 1], "-1")
        self.assertIn("pipe:1", command)
        self.assertEqual(command[command.index("-f") + 1], "mp3")
        self.assertNotIn("-ar", command)
        self.assertNotIn("-ac", command)

    def test_prepare_gebruikt_jobmap_en_verwijdert_alleen_tmp(self):
        job = type("Job", (), {"job_id": "abc", "download_path": str(self.source)})()
        processor = AudioProcessor(self.config, SequenceProbe(), AudioValidator())
        root = self.root / "downloads" / "processed"
        workspace = root / "abc"; workspace.mkdir(parents=True)
        (workspace / "processing.tmp.mp3").write_bytes(b"partial")
        final = workspace / "processed_audio.mp3"; final.write_bytes(b"valid")
        prepared = processor.prepare(job, root)
        self.assertFalse(prepared.temporary_path.exists())
        self.assertTrue(final.exists())

    def test_succesvolle_processing_valideert_en_renamed_atomisch(self):
        probe = SequenceProbe(
            probe_result(format_name="webm", audio_codec="opus"),
            probe_result(duration=101.0),
        )
        processor = AudioProcessor(
            self.config, probe, AudioValidator(), popen=FinishedProcess
        )
        prepared = self.prepared()
        result = processor.process(prepared)
        self.assertTrue(result.processed_path.is_file())
        self.assertFalse(prepared.temporary_path.exists())
        self.assertEqual(result.codec, "mp3")
        self.assertEqual(result.source_format, "webm")

    def test_ontbrekende_ffmpeg_en_ffprobe(self):
        missing = AudioProcessingConfig(self.root / "missing", self.ffprobe)
        processor = AudioProcessor(missing, SequenceProbe(), AudioValidator())
        with self.assertRaises(FfmpegNotFoundError):
            processor.build_command(self.prepared())
        with self.assertRaises(FfprobeNotFoundError):
            AudioProbe(self.root / "missing").inspect(self.source)

    def test_timeout_verwijdert_tijdelijk_bestand_en_behoudt_bron(self):
        config = AudioProcessingConfig(
            self.ffmpeg, self.ffprobe, processing_timeout_seconds=0
        )
        prepared = self.prepared()
        processor = AudioProcessor(
            config, SequenceProbe(probe_result()), AudioValidator(),
            popen=HangingProcess,
        )
        with self.assertRaises(FfmpegTimeoutError):
            processor.process(prepared)
        self.assertFalse(prepared.temporary_path.exists())
        self.assertTrue(self.source.exists())

    def test_annuleren_terminates_en_ffmpeg_failure_wordt_opgeruimd(self):
        prepared = self.prepared()
        processor = AudioProcessor(
            self.config, SequenceProbe(probe_result()), AudioValidator(),
            popen=HangingProcess,
        )
        Thread(target=lambda: (time.sleep(.02), processor.cancel()), daemon=True).start()
        with self.assertRaises(FfmpegCancelledError):
            processor.process(prepared)
        self.assertFalse(prepared.temporary_path.exists())
        failed = AudioProcessor(
            self.config, SequenceProbe(probe_result()), AudioValidator(),
            popen=lambda command, **kwargs: FinishedProcess(
                command, returncode=1, **kwargs
            ),
        )
        with self.assertRaises(FfmpegFailedError):
            failed.process(prepared)
        self.assertFalse(prepared.temporary_path.exists())

    def test_validator_empty_video_codec_duration_and_warning(self):
        validator = AudioValidator()
        empty = self.root / "empty.mp3"; empty.touch()
        with self.assertRaises(EmptyOutputError):
            validator.validate(empty, probe_result(), 100)
        output = self.root / "output.mp3"; output.write_bytes(b"x")
        with self.assertRaises(VideoStreamPresentError):
            validator.validate(output, probe_result(video_stream_count=1), 100)
        with self.assertRaises(WrongCodecError):
            validator.validate(output, probe_result(audio_codec="aac"), 100)
        with self.assertRaises(WrongFormatError):
            validator.validate(output, probe_result(format_name="matroska"), 100)
        validator.validate(output, probe_result(duration=103), 100)
        with self.assertRaises(DurationMismatchError):
            validator.validate(output, probe_result(duration=104), 100)
        result = validator.validate(output, probe_result(duration=1), None)
        self.assertTrue(result.warnings)

    def test_ffprobe_json_defensief_parsen(self):
        result = AudioProbe.parse({
            "format": {"format_name": "mp3", "duration": "12.5", "size": "42"},
            "streams": [
                {"codec_type": "audio", "codec_name": "mp3",
                 "sample_rate": "48000", "channels": 2, "bit_rate": "320000"},
                {"codec_type": "video", "codec_name": "png"},
            ],
        })
        self.assertEqual(result.duration, 12.5)
        self.assertEqual(result.audio_stream_count, 1)
        self.assertEqual(result.video_stream_count, 1)
        self.assertEqual(result.sample_rate, 48000)

    def test_stderr_limit_disk_full_permission_en_defaults(self):
        self.assertLessEqual(len(beperk_stderr("x" * 20000)), 8020)
        self.assertEqual(classify_processing_os_error(OSError(28, "full")).code, "DISK_FULL")
        self.assertEqual(classify_processing_os_error(PermissionError()).code, "PERMISSION_DENIED")
        config = AudioProcessingConfig()
        self.assertEqual(config.output_audio_format, "mp3")
        self.assertEqual(config.mp3_bitrate_kbps, 320)
        self.assertEqual(config.processing_timeout_seconds, 1800)
        self.assertTrue(config.keep_source_after_processing)

    def test_geen_metadata_artwork_of_verplaatsings_api(self):
        processor = AudioProcessor(self.config, SequenceProbe(), AudioValidator())
        for name in ("write_id3", "embed_artwork", "move_to_week", "finalize_recovery"):
            self.assertFalse(hasattr(processor, name))


if __name__ == "__main__":
    unittest.main()
