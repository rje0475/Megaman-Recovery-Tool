"""Qt-worker die de persistente downloadqueue sequentieel uitvoert."""

import traceback
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from core.download.engine import DownloadEngine
from core.download.errors import DownloadCancelled, DownloadError
from core.download.provider import YtDlpDownloadProvider
from core.download_queue import DownloadQueueManager
from database import SQLiteDatabase


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
        self, database_path, temp_root, provider_factory=None, parent=None
    ):
        super().__init__(parent)
        self.database_path = database_path
        self.temp_root = Path(temp_root)
        self.provider_factory = provider_factory or YtDlpDownloadProvider
        self._pause_requested = Event()
        self._stop_requested = Event()
        self._engine = None
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
                provider = self.provider_factory()
                self._engine = DownloadEngine(
                    manager, provider, self.temp_root, self.log_message.emit
                )
                self.job_started.emit(job.job_id)
                try:
                    self._engine.execute(job, self.job_progress.emit)
                except DownloadCancelled:
                    self.job_cancelled.emit(job.job_id)
                except DownloadError as error:
                    self.job_failed.emit(job.job_id, error.code, str(error))
                else:
                    self.job_completed.emit(job.job_id)
                finally:
                    self._engine = None
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

    def request_stop(self):
        self._stop_requested.set()
        self.request_cancel()
