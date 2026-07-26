"""Download Queue-pagina met een lokale statussimulator, zonder downloads."""

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.download_queue import (
    CANCELLED, COMPLETED, FAILED, PAUSED, PREPARING, QUEUED, RUNNING,
    WAITING, DownloadQueueManager,
)
from database import DATABASE_BESTAND, SQLiteDatabase


class DownloadQueueSimulator(QObject):
    """Simuleert uitsluitend de statusmachine met een QTimer."""

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
    ):
        super().__init__(parent)
        self.database = database_factory(database_path)
        self.logs = []
        self.manager = DownloadQueueManager(self.database, self._log)
        self.simulator = DownloadQueueSimulator(
            self.manager, simulator_interval_ms, self
        )
        self.simulator.changed.connect(self.refresh)
        self.simulator.completed.connect(self.refresh)
        self.setWindowTitle("Download Queue — simulatie")
        self.resize(1050, 620)
        self._build()
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        self.notice = QLabel(
            "Simulatiemodus: er wordt geen netwerk, yt-dlp, FFmpeg of "
            "audiobestand gebruikt."
        )
        layout.addWidget(self.notice)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels((
            "Positie", "Status", "Artiest", "Titel", "Bron",
            "Voortgang", "Retries", "Laatste fout",
        ))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, stretch=1)
        buttons = QHBoxLayout()
        specs = (
            ("Start Queue", self.start_queue), ("Pause", self.pause),
            ("Resume", self.resume), ("Retry Failed", self.retry_failed),
            ("Remove Selected", self.remove_selected),
            ("Clear Completed", self.clear_completed),
            ("Move Up", self.move_up), ("Move Down", self.move_down),
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
        jobs = self.manager.jobs()
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = (
                job.queue_position, job.status, job.artist, job.title,
                job.source_type, f"{job.progress}%",
                f"{job.retry_count}/{job.max_retries}",
                job.error_message or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(256, job.job_id)
                self.table.setItem(row, column, item)
        counts = {status: sum(job.status == status for job in jobs) for status in (
            WAITING, RUNNING, COMPLETED, FAILED, CANCELLED,
        )}
        self.summary_label.setText(
            f"Totale queue: {len(jobs)} | Waiting: {counts[WAITING]} | "
            f"Running: {counts[RUNNING]} | Completed: {counts[COMPLETED]} | "
            f"Failed: {counts[FAILED]} | Cancelled: {counts[CANCELLED]}"
        )
        self.total_progress.setValue(
            round(sum(job.progress for job in jobs) / len(jobs)) if jobs else 0
        )

    def selected_job_ids(self):
        return tuple(dict.fromkeys(
            self.table.item(index.row(), 0).data(256)
            for index in self.table.selectionModel().selectedRows()
        ))

    def selected_job_id(self):
        ids = self.selected_job_ids()
        return ids[0] if ids else None

    def start_queue(self):
        self.simulator.start()

    def pause(self):
        self.manager.pause()
        self.simulator.stop()
        self.refresh()

    def resume(self):
        self.manager.resume()
        self.simulator.start()
        self.refresh()

    def retry_failed(self):
        self.manager.retry_failed()
        self.refresh()

    def remove_selected(self):
        self.manager.remove_selected(self.selected_job_ids())
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

    def _log(self, message):
        self.logs.append(message)

    def closeEvent(self, event):
        self.simulator.stop()
        self.database.sluit()
        super().closeEvent(event)

    def done(self, result):
        self.simulator.stop()
        self.database.sluit()
        super().done(result)
