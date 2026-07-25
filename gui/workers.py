"""Qt-worker voor lange, bestaande Megaman-acties."""

import traceback

from PySide6.QtCore import QThread, Signal

from gui.workflow import WorkflowCallbacks


class _SignalWriter:
    def __init__(self, signaal):
        self.signaal = signaal

    def write(self, tekst):
        if tekst:
            self.signaal.emit(str(tekst))
        return len(tekst or "")

    def flush(self):
        pass


class ActionWorker(QThread):
    """Voer een callable uit zonder de Qt-eventloop te blokkeren."""

    log = Signal(str)
    progress = Signal(int)
    succeeded = Signal(object)
    failed = Signal(str)
    completed = Signal()

    def __init__(self, actie, *args, parent=None, **kwargs):
        super().__init__(parent)
        self.actie = actie
        self.args = args
        self.kwargs = kwargs

    def run(self):
        self.progress.emit(0)
        try:
            kwargs = dict(self.kwargs)
            kwargs.setdefault("uitvoer", _SignalWriter(self.log))
            resultaat = self.actie(*self.args, **kwargs)
            self.progress.emit(100)
            self.succeeded.emit(resultaat)
        except Exception as fout:
            self.failed.emit(str(fout) or type(fout).__name__)
        finally:
            self.completed.emit()


class WorkflowWorker(QThread):
    """Voer de centrale workflow uit en publiceer uitsluitend Qt-signalen."""

    stage_started = Signal(str)
    stage_progress = Signal(str, int, int, str)
    stage_completed = Signal(str)
    stage_skipped = Signal(str, str)
    stage_failed = Signal(str, str)
    log_message = Signal(str)
    workflow_completed = Signal(object)
    workflow_failed = Signal(str)

    def __init__(self, workflow, source, parent=None):
        super().__init__(parent)
        self.workflow = workflow
        self.source = source

    def run(self):
        current_stage = {"name": None}

        def stage_started(stage):
            current_stage["name"] = stage
            self.stage_started.emit(stage)

        callbacks = WorkflowCallbacks(
            stage_started=stage_started,
            stage_progress=self.stage_progress.emit,
            stage_completed=self.stage_completed.emit,
            stage_skipped=self.stage_skipped.emit,
            stage_failed=self.stage_failed.emit,
            log_message=self.log_message.emit,
        )
        try:
            summary = self.workflow.run(self.source, callbacks)
        except Exception as error:
            if current_stage["name"]:
                self.stage_failed.emit(
                    current_stage["name"],
                    str(error) or type(error).__name__,
                )
            self.log_message.emit(traceback.format_exc())
            self.workflow_failed.emit(
                str(error) or type(error).__name__
            )
        else:
            self.workflow_completed.emit(summary)
