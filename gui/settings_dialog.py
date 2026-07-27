"""PySide6-instellingenvenster voor de centrale SettingsManager."""

import logging
import re
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.settings import SettingsValidationError, get_settings_manager


SCHEMA = {
    "General": (("language", "Taal", "text"), ("theme", "Thema", ("system", "light", "dark")),
                ("log_level", "Logniveau", ("DEBUG", "INFO", "WARNING", "ERROR")),
                ("automatic_updates", "Automatische updates", "bool"),
                ("worker_threads", "Workerthreads", "int"),
                ("parallel_downloads", "Parallelle downloads", "int"),
                ("delete_original_rars_after_success",
                 "Originele RAR-bestanden verwijderen na succesvolle recovery",
                 "bool")),
    "Spotify": (("client_id", "Client ID", "text"), ("client_secret", "Client Secret", "password"),
                ("redirect_uri", "Redirect URI", "text"),
                ("playlist_name_template", "Playlistnaam-template", "text"),
                ("auto_select_high_confidence", "Automatisch hoge confidence", "bool"),
                ("confidence_threshold", "Confidence-drempel", "int"),
                ("artwork_resolution", "Artwork-resolutie", ("highest", "medium", "small"))),
    "YouTube": (("api_key", "API-key", "password"),
                ("max_candidates", "Maximaal kandidaten", "int"),
                ("confidence_threshold", "Confidence-drempel", "int"),
                ("preferred_provider", "Voorkeursprovider", ("official_api",)),
                ("prefer_official_channels", "Officiële kanalen bevoordelen", "bool")),
    "Downloads": (("download_dir", "Downloadmap", "text"),
                  ("processed_dir", "Processed-map", "text"),
                  ("output_root", "Eindmap", "text"),
                  ("concurrent_downloads", "Gelijktijdige downloads", "int"),
                  ("retries", "Retries", "int"), ("timeout_seconds", "Timeout", "int"),
                  ("retry_delay_seconds", "Retry-delay", "int")),
    "Audio": (("ffmpeg_path", "FFmpeg-pad", "text"), ("ffprobe_path", "ffprobe-pad", "text"),
              ("output_format", "Uitvoerformaat", ("mp3",)),
              ("bitrate_kbps", "Bitrate (kbps)", "int"),
              ("keep_source", "Bron behouden", "bool"),
              ("processing_timeout_seconds", "Processing-timeout", "int")),
    "Metadata": (("filename_template", "Bestandsnaam-template", "text"),
                 ("folder_template", "Map-template", "text"),
                 ("collision_policy", "Bestaand bestand", ("Rename", "Overwrite", "Skip")),
                 ("write_genre", "Genre schrijven", "bool"),
                 ("write_artwork", "Artwork schrijven", "bool"),
                 ("write_comments", "Comment schrijven", "bool"),
                 ("write_track_number", "Tracknummer schrijven", "bool"),
                 ("write_year", "Jaar schrijven", "bool")),
}

LOGGER = logging.getLogger(__name__)
DIAGNOSTIC_NAMES = (
    "Python", "SQLite", "Mutagen", "yt-dlp", "FFmpeg", "ffprobe",
    "Spotify auth", "YouTube API",
)
DIAGNOSTIC_DISPLAY_NAMES = {
    "Spotify auth": "Spotify",
    "YouTube API": "YouTube",
}
STATUS_COLORS = {
    "PASS": QColor("#187a27"),
    "WARNING": QColor("#9a6700"),
    "ERROR": QColor("#b00020"),
    "Niet gecontroleerd": QColor("#666666"),
}


class SettingsDialog(QDialog):
    def __init__(self, parent=None, manager=None, action_started_at=None):
        self._settings_started_at = action_started_at or time.monotonic()
        self._visible_logged = False
        self._mark_timing("SettingsDialog constructie gestart")
        super().__init__(parent)
        self.manager = manager or get_settings_manager()
        snapshot = self.manager.settings.as_dict()
        self._mark_timing("configuratie geladen")
        self.widgets = {}
        self.setWindowTitle("Settings")
        self.resize(680, 560)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._build_tabs(snapshot)
        self._mark_timing("widgets aangemaakt")
        self._build_diagnostics()
        self._mark_timing("providers/services niet geïnitialiseerd (lazy)")
        self.validation_label = QLabel()
        layout.addWidget(self.validation_label)
        actions = QHBoxLayout()
        for text, slot in (("Export Settings", self.export_settings),
                           ("Import Settings", self.import_settings),
                           ("Reset to Defaults", self.reset_defaults)):
            button = QPushButton(text); button.clicked.connect(slot); actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._validate_live()

    def _mark_timing(self, stage):
        elapsed = (time.monotonic() - self._settings_started_at) * 1000
        LOGGER.info("Settings startup: %s; elapsed_ms=%.1f", stage, elapsed)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._visible_logged:
            self._visible_logged = True
            self._mark_timing("dialog zichtbaar; totale duur")

    def _build_tabs(self, snapshot):
        section_names = {"General": "general", "Spotify": "spotify", "YouTube": "youtube",
                         "Downloads": "download", "Audio": "audio", "Metadata": "metadata"}
        for tab_name, fields in SCHEMA.items():
            section = section_names[tab_name]
            page = QWidget(); form = QFormLayout(page)
            for key, label, kind in fields:
                value = snapshot[section][key]
                widget = self._widget(kind, value)
                self.widgets[(section, key)] = widget
                form.addRow(label, widget)
            self.tabs.addTab(page, tab_name)

    def _widget(self, kind, value):
        if kind == "bool":
            widget = QCheckBox(); widget.setChecked(bool(value)); widget.toggled.connect(self._validate_live)
        elif kind == "int":
            widget = QSpinBox(); widget.setRange(0, 100000); widget.setValue(int(float(value))); widget.valueChanged.connect(self._validate_live)
        elif isinstance(kind, tuple):
            widget = QComboBox(); widget.addItems(kind); widget.setCurrentText(str(value)); widget.currentTextChanged.connect(self._validate_live)
        else:
            widget = QLineEdit(str(value)); widget.textChanged.connect(self._validate_live)
            if kind == "password": widget.setEchoMode(QLineEdit.EchoMode.Password)
        return widget

    def values(self):
        result = self.manager.settings.as_dict()
        for (section, key), widget in self.widgets.items():
            if isinstance(widget, QCheckBox): value = widget.isChecked()
            elif isinstance(widget, QSpinBox): value = widget.value()
            elif isinstance(widget, QComboBox): value = widget.currentText()
            else: value = widget.text().strip()
            result[section][key] = value
        return result

    def _validate_live(self, *_args):
        try:
            self.manager.validate(self.values())
        except SettingsValidationError as error:
            self.validation_label.setText(f"Ongeldig: {error}")
            self.validation_label.setStyleSheet("color: #b00020")
            return False
        self.validation_label.setText("Instellingen zijn geldig.")
        self.validation_label.setStyleSheet("color: #187a27")
        return True

    def validate_external(self):
        """Voer snelle padcontroles alleen na een expliciete gebruikersactie uit."""
        warnings = self.manager.validate_external()
        self.validation_label.setText(
            "Waarschuwing: " + warnings[0] if warnings
            else "Externe paden zijn geldig."
        )
        self.validation_label.setStyleSheet(
            "color: #9a6700" if warnings else "color: #187a27"
        )
        return not warnings

    def save(self):
        if not self._validate_live(): return
        for section in ("general", "spotify", "youtube", "download", "audio", "metadata"):
            self.manager.update_section(section, self.values()[section])
        self.accept()

    def export_settings(self):
        target, _ = QFileDialog.getSaveFileName(self, "Export Settings", "settings-export.json", "JSON (*.json)")
        if target: self.manager.export(target)

    def import_settings(self):
        source, _ = QFileDialog.getOpenFileName(self, "Import Settings", "", "JSON (*.json)")
        if not source: return
        try: self.manager.import_file(source)
        except Exception as error: QMessageBox.warning(self, "Import mislukt", str(error)); return
        QMessageBox.information(self, "Import voltooid", "Heropen Settings om alle waarden te tonen.")

    def reset_defaults(self):
        self.manager.reset()
        QMessageBox.information(self, "Defaults hersteld", "De standaardinstellingen zijn opgeslagen.")

    def _build_diagnostics(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        explanation = QLabel(
            "Diagnostiek wordt alleen uitgevoerd wanneer u op "
            "‘Diagnostiek vernieuwen’ klikt."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        table = QTableWidget(len(DIAGNOSTIC_NAMES), 3)
        table.setHorizontalHeaderLabels(("Onderdeel", "Status", "Details"))
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.diagnostic_labels = {}
        self.diagnostic_status_labels = {}
        for row, name in enumerate(DIAGNOSTIC_NAMES):
            display_name = DIAGNOSTIC_DISPLAY_NAMES.get(name, name)
            name_item = QTableWidgetItem(display_name)
            status_item = QTableWidgetItem("Niet gecontroleerd")
            status_item.setForeground(STATUS_COLORS["Niet gecontroleerd"])
            detail_item = QTableWidgetItem("Niet gecontroleerd")
            if display_name in {"Spotify", "YouTube"}:
                tooltip = "Configureer dit onderdeel via het bijbehorende tabblad."
                name_item.setToolTip(tooltip)
                detail_item.setToolTip(tooltip)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, status_item)
            table.setItem(row, 2, detail_item)
            self.diagnostic_status_labels[name] = status_item
            self.diagnostic_labels[name] = detail_item
        table.resizeRowsToContents()
        self.diagnostics_table = table
        layout.addWidget(table)

        self.diagnostics_details = QPlainTextEdit()
        self.diagnostics_details.setReadOnly(True)
        self.diagnostics_details.setPlaceholderText(
            "Vernieuw de diagnostiek om technische details te bekijken."
        )
        self.diagnostics_details.setVisible(False)
        self.diagnostics_details.setMaximumHeight(150)
        layout.addWidget(self.diagnostics_details)

        button_row = QHBoxLayout()
        refresh = QPushButton("Diagnostiek vernieuwen")
        refresh.clicked.connect(self.refresh_diagnostics)
        button_row.addWidget(refresh)
        validate = QPushButton("Externe paden controleren")
        validate.clicked.connect(self.validate_external)
        button_row.addWidget(validate)
        details = QPushButton("Details tonen")
        details.setCheckable(True)
        details.toggled.connect(self._toggle_diagnostic_details)
        button_row.addWidget(details)
        layout.addLayout(button_row)
        layout.addStretch(1)
        self.diagnostics_refresh_button = refresh
        self.external_validation_button = validate
        self.diagnostics_details_button = details
        self.tabs.addTab(page, "Diagnostics")

    @staticmethod
    def _diagnostic_presentation(name, raw_value):
        raw = str(raw_value or "onbekend").strip()
        folded = raw.casefold()
        if "niet geconfigureerd" in folded:
            return "WARNING", "Niet geconfigureerd"
        if "niet gevonden" in folded or "niet geïnstalleerd" in folded:
            return "WARNING", "Niet gevonden"
        if folded.startswith(("error", "fout")):
            return "ERROR", "Controle mislukt"
        if name in {"FFmpeg", "ffprobe"}:
            match = re.search(r"(?i)version\s+([^\s]+)", raw)
            return "PASS", f"Versie {match.group(1)}" if match else "Gevonden"
        if name in {"Spotify auth", "YouTube API"}:
            return "PASS", "Geconfigureerd"
        summary = raw if len(raw) <= 72 else raw[:69].rstrip() + "…"
        return "PASS", summary

    def _toggle_diagnostic_details(self, visible):
        self.diagnostics_details.setVisible(visible)
        self.diagnostics_details_button.setText(
            "Details verbergen" if visible else "Details tonen"
        )

    def refresh_diagnostics(self):
        """Zware versie- en toolchecks draaien uitsluitend expliciet."""
        self.diagnostics_refresh_button.setEnabled(False)
        try:
            values = self.manager.diagnostics()
            technical_details = []
            audio = self.manager.section("audio")
            for name, detail_item in self.diagnostic_labels.items():
                raw = values.get(name, "onbekend")
                status, summary = self._diagnostic_presentation(name, raw)
                status_item = self.diagnostic_status_labels[name]
                status_item.setText(status)
                status_item.setForeground(STATUS_COLORS[status])
                detail_item.setText(summary)
                detail_item.setToolTip(str(raw))
                technical_details.append(
                    f"{DIAGNOSTIC_DISPLAY_NAMES.get(name, name)}\n{raw}"
                )
            for label, key in (("FFmpeg-pad", "ffmpeg_path"),
                               ("ffprobe-pad", "ffprobe_path")):
                technical_details.append(
                    f"{label}\n{audio.get(key) or 'Niet expliciet ingesteld'}"
                )
            self.diagnostics_details.setPlainText("\n\n".join(technical_details))
        finally:
            self.diagnostics_refresh_button.setEnabled(True)
