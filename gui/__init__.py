"""Lazy entrypoint voor de optionele PySide6-interface."""

_active_window = None


class GuiDependencyFout(RuntimeError):
    """PySide6 is niet beschikbaar voor de GUI."""


def start_gui(
    argv=None, application_factory=None, window_factory=None,
    exception_installer=None, trace=None, fatal_handler=None,
):
    """Start Qt pas nadat de gebruiker expliciet `--gui` koos."""

    global _active_window
    from core.reliability.startup import get_startup_trace, show_fatal_startup_error
    trace = trace or get_startup_trace()
    fatal_handler = fatal_handler or show_fatal_startup_error
    trace.write("08 GUI import started")
    app = None
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from gui.main_window import MegamanMainWindow
    except ImportError as fout:
        error = GuiDependencyFout(
            "PySide6 ontbreekt. Installeer de GUI-afhankelijkheden met "
            "'python -m pip install -r requirements.txt'."
        )
        trace.exception("GUI import", fout)
        fatal_handler(error, trace.path)
        raise error from fout

    trace.write("09 GUI import completed")
    try:
        app = (
            application_factory(argv or []) if application_factory
            else QApplication.instance() or QApplication(argv or [])
        )
        app.setQuitOnLastWindowClosed(True)
        trace.write("10 QApplication created")
        if exception_installer is None:
            from core.reliability import install_global_exception_handler
            exception_installer = install_global_exception_handler
        exception_installer(
            user_notifier=lambda message: QMessageBox.critical(
                None, "Onverwachte fout", message
            )
        )
        trace.write("11 main window construction started")
        _active_window = (window_factory or MegamanMainWindow)()
        trace.write("12 main window constructed")
        _active_window.show()
        trace.write("13 main window shown")
        trace.write("14 event loop starting")
        result = app.exec()
        trace.write(f"15 event loop returned: {result}")
        _active_window = None
        return result
    except BaseException as error:
        trace.exception("GUI startup", error)
        fatal_handler(
            error, trace.path, qapplication=app, messagebox=QMessageBox
        )
        raise
