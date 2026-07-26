"""About- en healthdialogen voor de release candidate."""

import sqlite3
import sys

from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from core.release import run_health_check, run_self_test
from core.settings import get_settings_manager
from core.version import APP_NAME, VersionInfo


def _package_version(module_name, attribute="__version__"):
    try:
        module = __import__(module_name)
        if module_name == "yt_dlp": return module.version.__version__
        value = getattr(module, attribute, None)
        if value is None and module_name == "mutagen": value = getattr(module, "version_string", None)
        return str(value or "onbekend")
    except ImportError:
        return "niet geïnstalleerd"


class AboutDialog(QDialog):
    def __init__(self, parent=None, settings_manager=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        info = VersionInfo.current()
        diagnostics = (settings_manager or get_settings_manager()).diagnostics()
        layout = QVBoxLayout(self); form = QFormLayout()
        values = (
            ("Applicatie", APP_NAME), ("Versie", info.version), ("Build", info.build),
            ("Builddatum", info.build_date), ("Git commit", info.commit),
            ("Python", sys.version.split()[0]), ("Qt", qVersion()),
            ("SQLite", sqlite3.sqlite_version), ("yt-dlp", _package_version("yt_dlp")),
            ("FFmpeg", diagnostics["FFmpeg"]), ("Mutagen", _package_version("mutagen")),
            ("Licentie", "MIT License"),
        )
        for name, value in values: form.addRow(name, QLabel(str(value)))
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject); buttons.clicked.connect(self.accept)
        layout.addWidget(buttons)


class HealthCheckDialog(QDialog):
    def __init__(self, parent=None, self_test=False, runner=None):
        super().__init__(parent)
        self.setWindowTitle("Self Test" if self_test else "Project Health Check")
        report = (runner or (run_self_test if self_test else run_health_check))()
        layout = QVBoxLayout(self)
        heading = QLabel(f"Eindstatus: {report.status}"); heading.setObjectName("healthStatus")
        layout.addWidget(heading)
        form = QFormLayout()
        for item in report.items:
            label = QLabel(f"{item.status}: {item.message}"); label.setWordWrap(True)
            form.addRow(item.name, label)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject); buttons.clicked.connect(self.accept)
        layout.addWidget(buttons)
