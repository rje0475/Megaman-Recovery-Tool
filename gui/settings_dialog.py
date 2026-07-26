"""PySide6-instellingenvenster voor de centrale SettingsManager."""

import logging
import time

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from core.settings import SettingsValidationError, get_settings_manager


SCHEMA = {
    "General": (("language", "Taal", "text"), ("theme", "Thema", ("system", "light", "dark")),
                ("log_level", "Logniveau", ("DEBUG", "INFO", "WARNING", "ERROR")),
                ("automatic_updates", "Automatische updates", "bool"),
                ("worker_threads", "Workerthreads", "int"),
                ("parallel_downloads", "Parallelle downloads", "int")),
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
        form = QFormLayout()
        self.diagnostic_labels = {}
        for name in DIAGNOSTIC_NAMES:
            label = QLabel("Niet gecontroleerd")
            self.diagnostic_labels[name] = label
            form.addRow(name, label)
        layout.addLayout(form)
        refresh = QPushButton("Diagnostiek vernieuwen")
        refresh.clicked.connect(self.refresh_diagnostics)
        layout.addWidget(refresh)
        validate = QPushButton("Externe paden controleren")
        validate.clicked.connect(self.validate_external)
        layout.addWidget(validate)
        layout.addStretch(1)
        self.diagnostics_refresh_button = refresh
        self.external_validation_button = validate
        self.tabs.addTab(page, "Diagnostics")

    def refresh_diagnostics(self):
        """Zware versie- en toolchecks draaien uitsluitend expliciet."""
        self.diagnostics_refresh_button.setEnabled(False)
        try:
            values = self.manager.diagnostics()
            for name, label in self.diagnostic_labels.items():
                label.setText(values.get(name, "onbekend"))
        finally:
            self.diagnostics_refresh_button.setEnabled(True)
