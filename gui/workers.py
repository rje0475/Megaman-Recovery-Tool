"""Qt-worker voor lange, bestaande Megaman-acties."""

from PySide6.QtCore import QThread, Signal


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
