"""QObject-worker voor de begeleide GUI-workflow."""

import traceback
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from gui.workflow import WorkflowCallbacks
from core.youtube.search import zoek_youtube_kandidaten
from database import SQLiteDatabase


class WorkflowWorker(QObject):
    stage_started = Signal(str)
    stage_progress = Signal(object)
    stage_completed = Signal(str)
    stage_skipped = Signal(str, str)
    stage_failed = Signal(str, str)
    log_message = Signal(str)
    workflow_completed = Signal(object)
    workflow_failed = Signal(str)
    review_requested = Signal(object)
    finished = Signal()

    def __init__(self, workflow, source, parent=None):
        super().__init__(parent)
        self.workflow = workflow
        self.source = source
        self._review_event = Event()
        self._review_result = False
        self.setObjectName("MegamanWorkflowWorker")

    @Slot()
    def run(self):
        current_stage = {"name": None}

        def stage_started(stage):
            current_stage["name"] = stage
            self.stage_started.emit(stage)

        def stage_completed(stage):
            self.stage_completed.emit(stage)
            if current_stage["name"] == stage:
                current_stage["name"] = None

        def stage_skipped(stage, reason):
            self.stage_skipped.emit(stage, reason)
            if current_stage["name"] == stage:
                current_stage["name"] = None

        callbacks = WorkflowCallbacks(
            stage_started=stage_started,
            stage_progress=self.stage_progress.emit,
            stage_completed=stage_completed,
            stage_skipped=stage_skipped,
            stage_failed=self.stage_failed.emit,
            log_message=self.log_message.emit,
            review_requested=self._request_review,
        )
        try:
            summary = self.workflow.run(self.source, callbacks)
        except Exception as error:
            melding = str(error) or type(error).__name__
            if current_stage["name"]:
                self.stage_failed.emit(current_stage["name"], melding)
            self.log_message.emit(traceback.format_exc())
            self.workflow_failed.emit(melding)
        else:
            self.workflow_completed.emit(summary)
        finally:
            self.finished.emit()

    def _request_review(self, summary):
        self._review_result = False
        self._review_event.clear()
        self.review_requested.emit(summary)
        self._review_event.wait()
        return self._review_result

    def resolve_review(self, accepted):
        """Thread-safe: alleen eenvoudige waarden plus threading.Event."""
        self._review_result = accepted
        self._review_event.set()


class YouTubeSearchWorker(QObject):
    """Korte, afzonderlijke netwerkworker voor de Review Wizard."""

    completed = Signal(object, bool)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, database_path, recovery_item_id, provider_factory, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.recovery_item_id = int(recovery_item_id)
        self.provider_factory = provider_factory
        self.setObjectName("YouTubeSearchWorker")

    @Slot()
    def run(self):
        database = None
        try:
            database = SQLiteDatabase(self.database_path)
            candidates, selected_missing = zoek_youtube_kandidaten(
                database, self.recovery_item_id,
                provider=self.provider_factory(), search_again=True,
            )
            self.completed.emit(tuple(dict(row) for row in candidates), selected_missing)
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__)
        finally:
            if database is not None:
                database.sluit()
            self.finished.emit()
