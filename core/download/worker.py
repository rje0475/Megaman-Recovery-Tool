"""Qt-worker die de persistente downloadqueue sequentieel uitvoert."""

import traceback
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from core.download.engine import DownloadEngine
from core.download.errors import DownloadCancelled, DownloadError
from core.download.provider import YtDlpDownloadProvider
from core.download_queue import CANCELLED, DownloadQueueManager
from core.audio.errors import AudioProcessingError, FfmpegCancelledError
from core.audio.models import AudioProcessingConfig, AudioProcessingProgress
from core.audio.processor import AudioProcessor
from core.audio.probe import AudioProbe
from core.audio.validator import AudioValidator
from core.metadata import MetadataConfig, MetadataFinalizer
from core.metadata.errors import FinalizationSkipped, MetadataError
from database import SQLiteDatabase
from core.settings import get_settings_manager


class DownloadQueueWorker(QObject):
    job_started = Signal(str)
    job_progress = Signal(str, object)
    job_completed = Signal(str)
    job_failed = Signal(str, str, str)
    job_cancelled = Signal(str)
    log_message = Signal(str)
    queue_completed = Signal()
    finished = Signal()

    def __init__(
        self, database_path, temp_root, provider_factory=None,
        audio_config=None, processor_factory=None, metadata_config=None,
        finalizer_factory=None, parent=None
    ):
        super().__init__(parent)
        self.database_path = database_path
        settings = get_settings_manager()
        self.temp_root = Path(temp_root or settings.section("download")["download_dir"])
        self.provider_factory = provider_factory or YtDlpDownloadProvider
        self.audio_config = audio_config or AudioProcessingConfig.from_environment()
        self.processor_factory = processor_factory
        self.metadata_config = metadata_config or MetadataConfig.from_environment()
        self.finalizer_factory = finalizer_factory
        self.processed_root = (Path(settings.section("download")["processed_dir"])
                               if temp_root is None else self.temp_root.parent / "processed")
        self._pause_requested = Event()
        self._stop_requested = Event()
        self._engine = None
        self._audio_processor = None
        self._finalizer = None
        self.current_job_id = None
        self.setObjectName("DownloadQueueWorker")

    @Slot()
    def run(self):
        database = None
        try:
            database = SQLiteDatabase(self.database_path)
            manager = DownloadQueueManager(database, self.log_message.emit)
            while not self._stop_requested.is_set():
                if self._pause_requested.is_set():
                    break
                job = manager.dequeue()
                if job is None:
                    self.queue_completed.emit()
                    break
                self.current_job_id = job.job_id
                self.job_started.emit(job.job_id)
                try:
                    if not self._source_is_valid(job.download_path) and not self._source_is_valid(job.processed_path):
                        provider = self.provider_factory()
                        self._engine = DownloadEngine(
                            manager, provider, self.temp_root, self.log_message.emit
                        )
                        self._engine.execute(job, self.job_progress.emit)
                        job = manager.get(job.job_id)
                    if not self._source_is_valid(job.processed_path):
                        self._process_audio(manager, job)
                    job = manager.get(job.job_id)
                    if self._can_finalize(database, job.recovery_item_id):
                        self._finalize(manager, job, database)
                    else:
                        self.log_message.emit(
                            "Finalisatie overgeslagen: geen opgeslagen Spotify-match."
                        )
                except DownloadCancelled:
                    self.job_cancelled.emit(job.job_id)
                except FfmpegCancelledError as error:
                    manager.transition(
                        job.job_id, CANCELLED, "Audioverwerking geannuleerd",
                        error_code=error.code, error_message=str(error),
                    )
                    self.job_cancelled.emit(job.job_id)
                except AudioProcessingError as error:
                    manager.fail_processing(job.job_id, error)
                    if error.stderr:
                        self.log_message.emit(
                            f"FFmpeg/ffprobe technische uitvoer:\n{error.stderr}"
                        )
                    self.job_failed.emit(job.job_id, error.code, str(error))
                except FinalizationSkipped as error:
                    manager.fail_finalization(job.job_id, error)
                    self.job_failed.emit(job.job_id, error.code, str(error))
                except MetadataError as error:
                    manager.fail_finalization(job.job_id, error)
                    self.job_failed.emit(job.job_id, error.code, str(error))
                except DownloadError as error:
                    self.job_failed.emit(job.job_id, error.code, str(error))
                else:
                    self.job_completed.emit(job.job_id)
                finally:
                    self._engine = None
                    self._audio_processor = None
                    self._finalizer = None
                    self.current_job_id = None
        except Exception as error:
            self.log_message.emit(traceback.format_exc())
            self.job_failed.emit(
                self.current_job_id or "", "WORKER_ERROR",
                str(error) or type(error).__name__,
            )
        finally:
            if database is not None:
                database.sluit()
            self.finished.emit()

    def request_pause(self):
        """Laat de actieve job aflopen en start daarna geen nieuwe job."""
        self._pause_requested.set()

    def request_cancel(self, job_id=None):
        if self._engine and (
            job_id is None or job_id == self.current_job_id
        ):
            self._engine.cancel()
        if self._audio_processor and (
            job_id is None or job_id == self.current_job_id
        ):
            self._audio_processor.cancel()

    def request_stop(self):
        self._stop_requested.set()
        self.request_cancel()

    @staticmethod
    def _source_is_valid(path):
        try:
            return bool(path and Path(path).is_file() and Path(path).stat().st_size > 0)
        except OSError:
            return False

    def _make_processor(self):
        if self.processor_factory:
            return self.processor_factory()
        probe = AudioProbe(
            self.audio_config.ffprobe_path,
            timeout=min(120, self.audio_config.processing_timeout_seconds),
        )
        return AudioProcessor(
            self.audio_config, probe, AudioValidator()
        )

    def _process_audio(self, manager, job):
        self._audio_processor = self._make_processor()
        prepared = self._audio_processor.prepare(job, self.processed_root)
        if prepared.final_path.is_file():
            try:
                result = self._audio_processor.recognize_existing(
                    prepared, job.source_duration
                )
            except AudioProcessingError:
                pass
            else:
                manager.complete_processing(job.job_id, result)
                self.job_progress.emit(
                    job.job_id, AudioProcessingProgress(
                        100, "PROCESSED", "Bestaande uitvoer gevalideerd"
                    ),
                )
                return
        manager.start_processing(job.job_id, job.source_duration)

        def stage(stage_name):
            if stage_name == "VALIDATING":
                manager.start_validation(job.job_id)
            self.job_progress.emit(
                job.job_id, AudioProcessingProgress(
                    99 if stage_name == "VALIDATING" else 0,
                    stage_name, stage_name,
                ),
            )

        def progress(update):
            manager.update_processing_progress(job.job_id, update)
            self.job_progress.emit(job.job_id, update)

        result = self._audio_processor.process(
            prepared, progress_callback=progress, stage_callback=stage
        )
        manager.complete_processing(job.job_id, result)
        if not self.audio_config.keep_source_after_processing:
            try:
                prepared.source_path.unlink()
            except OSError as error:
                self.log_message.emit(
                    f"Bronbestand kon na geldige verwerking niet worden verwijderd: {error}"
                )
            else:
                manager.mark_source_removed(job.job_id)

    def _finalize(self, manager, job, database):
        self._finalizer = (
            self.finalizer_factory(database)
            if self.finalizer_factory else
            MetadataFinalizer(database, self.metadata_config)
        )
        manager.start_finalization(job.job_id)
        result = self._finalizer.finalize(job)
        manager.complete_finalization(job.job_id, result)

    @staticmethod
    def _can_finalize(database, recovery_item_id):
        row = database.verbinding.execute(
            """SELECT selected_spotify_candidate_id,selected_spotify_artist,
            selected_spotify_title FROM recovery_items WHERE id=?""",
            (recovery_item_id,),
        ).fetchone()
        return bool(row and row["selected_spotify_candidate_id"]
                    and row["selected_spotify_artist"] and row["selected_spotify_title"])
