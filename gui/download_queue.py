"""Download Queue-pagina voor tijdelijke onbewerkte bronaudio."""

from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.download.worker import DownloadQueueWorker
from core.download_queue import (
    CANCELLED, COMPLETED, DOWNLOADING, FAILED, FINALIZING, PAUSED, PREPARING,
    PROCESSED, QUEUED, RECOVERED, RUNNING, WAITING, DownloadQueueManager,
)
from database import DATABASE_BESTAND, SQLiteDatabase
from core.settings import get_settings_manager


class DownloadQueueSimulator(QObject):
    """Behoudt de deterministische statussimulator voor geïsoleerde tests."""

    changed = Signal()
    completed = Signal()

    def __init__(self, manager, interval_ms=80, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._tick)
        self.current_job_id = None

    @property
    def active(self):
        return self.timer.isActive()

    def start(self):
        if not self.timer.isActive():
            self.timer.start()

    def stop(self):
        self.timer.stop()
        self.current_job_id = None

    def _tick(self):
        job = self.manager.get(self.current_job_id) if self.current_job_id else None
        if job is None or job.status in {COMPLETED, CANCELLED, FAILED, PAUSED}:
            job = self.manager.dequeue()
            self.current_job_id = job.job_id if job else None
            if job is None:
                self.timer.stop()
                self.completed.emit()
                return
        if job.status == QUEUED:
            self.manager.transition(job.job_id, PREPARING, "Voorbereiden", 10)
        elif job.status == PREPARING:
            self.manager.transition(job.job_id, RUNNING, "Simulatie actief", 25)
        elif job.status == RUNNING and job.progress < 75:
            self.manager.transition(
                job.job_id, RUNNING, "Simulatie actief", job.progress + 25
            )
        elif job.status == RUNNING:
            self.manager.transition(job.job_id, COMPLETED, "Simulatie voltooid", 100)
            self.current_job_id = None
        self.changed.emit()


class DownloadQueueDialog(QDialog):
    def __init__(
        self, parent=None, database_path=DATABASE_BESTAND,
        database_factory=SQLiteDatabase, simulator_interval_ms=80,
        worker_factory=DownloadQueueWorker, temp_root=None,
    ):
        super().__init__(parent)
        self.database = database_factory(database_path)
        self.database_path = database_path
        self.worker_factory = worker_factory
        configured = get_settings_manager().section("download")["download_dir"]
        self.temp_root = Path(temp_root or configured)
        self.logs = []
        self.manager = DownloadQueueManager(self.database, self._log)
        self.simulator = DownloadQueueSimulator(
            self.manager, simulator_interval_ms, self
        )
        self.worker_thread = None
        self.worker = None
        self._closing = False
        self.setWindowTitle("Download Queue")
        self.resize(1180, 640)
        self._build()
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        self.notice = QLabel(
            "Onbewerkte bronaudio wordt tijdelijk opgeslagen onder "
            f"{self.temp_root}. Geen conversie, ID3-tags of verplaatsing "
            "naar de eindlocatie."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.table = QTableWidget(0, 25)
        self.table.setHorizontalHeaderLabels((
            "Positie", "Status", "Fase", "Artiest", "Titel",
            "Downloadstatus", "Processingstatus", "Voortgang", "KB/s", "ETA",
            "Brongrootte", "Bronbestand", "Verwerkt bestand", "Bronduur",
            "Verwerkte duur", "Codec", "Bitrate", "Sample rate", "Kanalen", "Retries",
            "Metadata", "Artwork", "Bestandsnaam", "Finale locatie",
            "Fout / waarschuwing",
        ))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, stretch=1)
        buttons = QHBoxLayout()
        specs = (
            ("Start Queue", self.start_queue), ("Pause", self.pause),
            ("Resume", self.resume), ("Retry Failed", self.retry_failed),
            ("Remove Selected", self.remove_selected),
            ("Clear Completed", self.clear_completed),
            ("Move Up", self.move_up), ("Move Down", self.move_down),
            ("Open bronmap", self.open_source_folder),
            ("Open verwerkingsmap", self.open_processed_folder),
            ("Open bestand", self.open_final_file),
            ("Open map", self.open_final_folder),
        )
        self.buttons = {}
        for text, slot in specs:
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
            self.buttons[text] = button
        layout.addLayout(buttons)
        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)
        self.total_progress = QProgressBar()
        self.total_progress.setRange(0, 100)
        layout.addWidget(self.total_progress)
        close = QPushButton("Terug naar review")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def refresh(self):
        if self._closing:
            return
        jobs = self.manager.jobs(limit=1000)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = (
                job.queue_position, job.status, self._phase(job),
                job.artist, job.title,
                job.status if job.status in {DOWNLOADING, "DOWNLOADED"} else "—",
                job.processing_status or "—", f"{job.progress}%",
                f"{job.speed_bytes_per_second / 1024:.1f}"
                if job.speed_bytes_per_second else "—",
                f"{job.eta_seconds}s" if job.eta_seconds is not None else "—",
                self._format_size(job.download_size),
                job.download_path or "—", job.processed_path or "—",
                self._format_duration(job.source_duration),
                self._format_duration(job.processed_duration),
                job.processed_codec or "—",
                f"{job.processed_bitrate / 1000:.0f} kbps"
                if job.processed_bitrate else "—",
                f"{job.processed_sample_rate} Hz"
                if job.processed_sample_rate else "—",
                job.processed_channels if job.processed_channels else "—",
                f"{job.retry_count}/{job.max_retries}",
                "Geschreven" if job.metadata_written else "—",
                "Geschreven" if job.artwork_written else "—",
                job.filename or "—", job.final_path or "—",
                job.error_message or job.processing_warning or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(256, job.job_id)
                self.table.setItem(row, column, item)
        total, counts, average = self.manager.queue_statistics()
        counts = {status: counts.get(status, 0) for status in (
            WAITING, RUNNING, DOWNLOADING, COMPLETED, PROCESSED, RECOVERED, FAILED, CANCELLED,
        )}
        self.summary_label.setText(
            f"Totale queue: {total} | getoond: {len(jobs)} | Waiting: {counts[WAITING]} | "
            f"Running: {counts[RUNNING] + counts[DOWNLOADING]} | "
            f"Completed: {counts[COMPLETED] + counts[PROCESSED] + counts[RECOVERED]} | "
            f"Failed: {counts[FAILED]} | "
            f"Cancelled: {counts[CANCELLED]}"
        )
        self.total_progress.setValue(average)
        self.table.setSortingEnabled(True)

    def selected_job_ids(self):
        return tuple(dict.fromkeys(
            self.table.item(index.row(), 0).data(256)
            for index in self.table.selectionModel().selectedRows()
        ))

    def selected_job_id(self):
        ids = self.selected_job_ids()
        return ids[0] if ids else None

    def start_queue(self):
        if self.worker_thread and self.worker_thread.isRunning():
            return
        thread = QThread(self)
        worker = self.worker_factory(self.database_path, self.temp_root)
        thread.setObjectName("DownloadQueueThread")
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.job_started.connect(self._job_event)
        worker.job_progress.connect(self._job_progress)
        worker.job_completed.connect(self._job_event)
        worker.job_failed.connect(self._job_event)
        worker.job_cancelled.connect(self._job_event)
        worker.log_message.connect(self._log)
        worker.queue_completed.connect(self.refresh)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._worker_finished)
        thread.finished.connect(thread.deleteLater)
        self.worker_thread, self.worker = thread, worker
        self.buttons["Start Queue"].setEnabled(False)
        thread.start()

    def pause(self):
        if self.worker and self.worker_thread and self.worker_thread.isRunning():
            self.worker.request_pause()
            self._log("Pauze aangevraagd; actieve download wordt afgemaakt.")

    def resume(self):
        self.start_queue()

    def retry_failed(self):
        self.manager.retry_failed()
        self.refresh()

    def remove_selected(self):
        for job_id in self.selected_job_ids():
            if self.worker and job_id == self.worker.current_job_id:
                self.worker.request_cancel(job_id)
            else:
                self.manager.cancel(job_id)
        self.refresh()

    def clear_completed(self):
        self.manager.clear_completed()
        self.refresh()

    def move_up(self):
        if self.manager.move_up(self.selected_job_id()):
            self.refresh()

    def move_down(self):
        if self.manager.move_down(self.selected_job_id()):
            self.refresh()

    def _job_progress(self, _job_id, _progress):
        self.refresh()

    def _job_event(self, *_args):
        self.refresh()

    def _log(self, message):
        self.logs.append(message)

    def _worker_finished(self):
        self.worker_thread = None
        self.worker = None
        self.buttons["Start Queue"].setEnabled(True)
        self.refresh()

    @staticmethod
    def _format_size(size):
        if size is None:
            return "—"
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / 1024:.1f} KB"

    @staticmethod
    def _format_duration(seconds):
        if seconds is None:
            return "—"
        return f"{seconds:.2f}s"

    @staticmethod
    def _phase(job):
        if job.status in {"PROCESSING"}:
            return "Verwerken"
        if job.status in {"VALIDATING"}:
            return "Valideren"
        if job.status in {"PROCESSED"}:
            return "Gereed"
        if job.status == FINALIZING:
            return "Finaliseren"
        if job.status == RECOVERED:
            return "Gereed"
        if job.status in {DOWNLOADING, PREPARING, QUEUED, RUNNING}:
            return "Downloaden"
        return "Wachten"

    def open_source_folder(self):
        self._open_job_folder("download_path")

    def open_processed_folder(self):
        self._open_job_folder("processed_path")

    def open_final_file(self):
        job = self.manager.get(self.selected_job_id())
        path = Path(job.final_path) if job and job.final_path else None
        if path and path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_final_folder(self):
        self._open_job_folder("final_path")

    def _open_job_folder(self, attribute):
        job = self.manager.get(self.selected_job_id())
        value = getattr(job, attribute, None) if job else None
        folder = Path(value).parent if value else None
        if folder and folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _shutdown_worker(self):
        if not self.worker_thread or not self.worker_thread.isRunning():
            return True
        self.worker.request_stop()
        if self.worker_thread.wait(5000):
            return True
        QMessageBox.information(
            self, "Download actief",
            "De actieve download wordt nog veilig gestopt. Probeer zo opnieuw.",
        )
        return False

    def closeEvent(self, event):
        self.simulator.stop()
        self._closing = True
        if not self._shutdown_worker():
            self._closing = False
            event.ignore()
            return
        self.database.sluit()
        super().closeEvent(event)

    def done(self, result):
        self.simulator.stop()
        self._closing = True
        if not self._shutdown_worker():
            self._closing = False
            return
        self.database.sluit()
        super().done(result)
