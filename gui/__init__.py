"""Lazy entrypoint voor de optionele PySide6-interface."""


class GuiDependencyFout(RuntimeError):
    """PySide6 is niet beschikbaar voor de GUI."""


def start_gui(argv=None):
    """Start Qt pas nadat de gebruiker expliciet `--gui` koos."""

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from gui.main_window import MegamanMainWindow
    except ImportError as fout:
        raise GuiDependencyFout(
            "PySide6 ontbreekt. Installeer de GUI-afhankelijkheden met "
            "'python -m pip install -r requirements.txt'."
        ) from fout

    app = QApplication.instance() or QApplication(argv or [])
    from core.reliability import install_global_exception_handler
    install_global_exception_handler(
        user_notifier=lambda message: QMessageBox.critical(
            None, "Onverwachte fout", message
        )
    )
    venster = MegamanMainWindow()
    venster.show()
    return app.exec()
