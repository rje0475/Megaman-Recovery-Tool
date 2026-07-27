import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from core.audio.models import AudioProcessingConfig
from core.metadata.models import MetadataConfig
from core.settings import SettingsManager, SettingsValidationError
from core.settings.defaults import CURRENT_VERSION, DEFAULTS
from gui.settings_dialog import SettingsDialog


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "config" / "settings.json"
        self.manager = SettingsManager(self.path, environment={})

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_are_created_and_versioned(self):
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.manager.settings.version, CURRENT_VERSION)
        self.assertEqual(self.manager.section("audio")["bitrate_kbps"], 320)
        self.assertEqual(self.manager.section("metadata")["collision_policy"], "Rename")

    def test_flat_version_zero_is_migrated_without_losing_values(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"ffmpeg_path": "C:/tools/ffmpeg.exe",
                                         "filename_template": "{title}.mp3"}), encoding="utf-8")
        migrated = SettingsManager(self.path, environment={})
        self.assertEqual(migrated.section("audio")["ffmpeg_path"], "C:/tools/ffmpeg.exe")
        self.assertEqual(migrated.section("metadata")["filename_template"], "{title}.mp3")

    def test_export_excludes_secrets_and_import_preserves_them(self):
        self.manager.update_section("spotify", {"client_id": "client-123456",
                                                 "client_secret": "secret-value"})
        self.manager.update_section("youtube", {"api_key": "youtube-secret-key-123456"})
        target = self.root / "export.json"
        self.manager.export(target)
        text = target.read_text(encoding="utf-8")
        self.assertNotIn("secret-value", text)
        self.assertNotIn("youtube-secret", text)
        exported = json.loads(text); exported["general"]["theme"] = "dark"
        target.write_text(json.dumps(exported), encoding="utf-8")
        self.manager.import_file(target)
        self.assertEqual(self.manager.section("spotify")["client_secret"], "secret-value")
        self.assertEqual(self.manager.section("general")["theme"], "dark")

    def test_reset_and_validation(self):
        self.manager.update_section("general", {"theme": "dark"})
        self.manager.reset()
        self.assertEqual(self.manager.section("general")["theme"], DEFAULTS["general"]["theme"])
        with self.assertRaises(SettingsValidationError):
            self.manager.update_section("metadata", {"collision_policy": "Destroy"})

    def test_environment_backwards_compatibility_uses_manager(self):
        env = {"FFMPEG_PATH": "C:/ffmpeg.exe", "MP3_BITRATE_KBPS": "192",
               "OUTPUT_ROOT": "Final", "FILENAME_TEMPLATE": "{title}.mp3"}
        audio = AudioProcessingConfig.from_environment(env, which=lambda _name: None)
        metadata = MetadataConfig.from_environment(env)
        self.assertEqual(audio.mp3_bitrate_kbps, 192)
        self.assertEqual(metadata.output_root, Path("Final"))
        self.assertEqual(metadata.filename_template, "{title}.mp3")

    def test_diagnostics_and_external_validation(self):
        diagnostics = self.manager.diagnostics()
        self.assertTrue({"Python", "SQLite", "Mutagen", "yt-dlp", "FFmpeg",
                         "Spotify auth", "YouTube API"} <= diagnostics.keys())
        self.manager.update_section("audio", {"ffmpeg_path": str(self.root / "missing.exe")})
        self.assertTrue(any("FFmpeg" in value for value in self.manager.validate_external()))

    def test_settings_gui_contains_all_tabs_and_live_validation(self):
        dialog = SettingsDialog(manager=self.manager)
        try:
            names = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
            self.assertEqual(names, ["General", "Spotify", "YouTube", "Downloads",
                                     "Audio", "Metadata", "Diagnostics"])
            dialog.widgets[("metadata", "filename_template")].setText("")
            self.assertIn("Ongeldig", dialog.validation_label.text())
        finally:
            dialog.close()

    def test_open_is_lazy_and_performs_no_external_operations(self):
        with patch("subprocess.run") as run, \
                patch("subprocess.Popen") as popen, \
                patch("urllib.request.urlopen") as urlopen, \
                patch("os.walk") as walk, \
                patch.object(Path, "rglob") as rglob, \
                patch.object(self.manager, "diagnostics") as diagnostics, \
                patch.object(self.manager, "validate_external") as external:
            dialog = SettingsDialog(manager=self.manager)
            try:
                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog.isVisible())
                run.assert_not_called()
                popen.assert_not_called()
                urlopen.assert_not_called()
                walk.assert_not_called()
                rglob.assert_not_called()
                diagnostics.assert_not_called()
                external.assert_not_called()
            finally:
                dialog.close()

    def test_saved_values_are_shown_without_tool_version_checks(self):
        self.manager.update_section("audio", {
            "ffmpeg_path": "C:/Tools/ffmpeg.exe",
            "ffprobe_path": "C:/Tools/ffprobe.exe",
        })
        with patch("core.settings.manager.subprocess.run") as run:
            dialog = SettingsDialog(manager=self.manager)
        try:
            self.assertEqual(
                dialog.widgets[("audio", "ffmpeg_path")].text(),
                "C:/Tools/ffmpeg.exe",
            )
            self.assertEqual(
                dialog.widgets[("audio", "ffprobe_path")].text(),
                "C:/Tools/ffprobe.exe",
            )
            run.assert_not_called()
        finally:
            dialog.close()

    def test_repeated_open_does_not_duplicate_heavy_initialization(self):
        with patch.object(self.manager, "diagnostics") as diagnostics:
            for _ in range(2):
                dialog = SettingsDialog(manager=self.manager)
                dialog.show()
                self.app.processEvents()
                dialog.close()
            diagnostics.assert_not_called()

    def test_explicit_diagnostic_and_path_buttons_still_work(self):
        values = {
            "Python": "3.x", "SQLite": "3.x", "Mutagen": "1.x",
            "yt-dlp": "1.x", "FFmpeg": "ffmpeg version 8.1.2 long build details",
            "ffprobe": "ffprobe version 8.1.2 long build details",
            "Spotify auth": "geconfigureerd", "YouTube API": "geconfigureerd",
        }
        with patch.object(self.manager, "diagnostics", return_value=values) as diagnostics, \
                patch.object(self.manager, "validate_external", return_value=()) as external:
            dialog = SettingsDialog(manager=self.manager)
            try:
                dialog.show()
                self.app.processEvents()
                dialog.diagnostics_refresh_button.click()
                dialog.external_validation_button.click()
                diagnostics.assert_called_once_with()
                external.assert_called_once_with()
                self.assertEqual(dialog.diagnostic_labels["FFmpeg"].text(), "Versie 8.1.2")
            finally:
                dialog.close()

    def test_diagnostics_initially_show_not_checked_with_accessible_status(self):
        dialog = SettingsDialog(manager=self.manager)
        try:
            self.assertTrue(dialog.diagnostics_refresh_button.isEnabled())
            self.assertTrue(dialog.external_validation_button.isEnabled())
            for name in dialog.diagnostic_labels:
                self.assertEqual(
                    dialog.diagnostic_status_labels[name].text(),
                    "Niet gecontroleerd",
                )
                self.assertEqual(
                    dialog.diagnostic_labels[name].text(),
                    "Niet gecontroleerd",
                )
        finally:
            dialog.close()

    def test_diagnostic_summary_is_short_and_full_details_remain_available(self):
        long_ffmpeg = "ffmpeg version 8.1.2 " + "technische-buildinformatie " * 20
        values = {
            "Python": "3.12.13", "SQLite": "3.50.4", "Mutagen": "1.48.1",
            "yt-dlp": "2026.07.04", "FFmpeg": long_ffmpeg,
            "ffprobe": "ffprobe version 8.1.2 volledige uitvoer",
            "Spotify auth": "niet geconfigureerd",
            "YouTube API": "niet geconfigureerd",
        }
        with patch.object(self.manager, "diagnostics", return_value=values):
            dialog = SettingsDialog(manager=self.manager)
            try:
                dialog.show()
                self.app.processEvents()
                dialog.diagnostics_refresh_button.click()
                self.assertEqual(dialog.diagnostic_labels["FFmpeg"].text(), "Versie 8.1.2")
                self.assertEqual(dialog.diagnostic_labels["ffprobe"].text(), "Versie 8.1.2")
                self.assertIn(long_ffmpeg, dialog.diagnostics_details.toPlainText())
                self.assertTrue(dialog.diagnostics_details.isHidden())
                dialog.diagnostics_details_button.click()
                self.assertFalse(dialog.diagnostics_details.isHidden())
                self.assertEqual(dialog.diagnostics_details_button.text(), "Details verbergen")
            finally:
                dialog.close()

    def test_statuses_are_textual_and_service_labels_are_consistent(self):
        values = {
            "Python": "3.12.13", "SQLite": "fout: beschadigd",
            "Mutagen": "niet geïnstalleerd", "yt-dlp": "2026.07.04",
            "FFmpeg": "ffmpeg version 8.1.2", "ffprobe": "niet gevonden",
            "Spotify auth": "niet geconfigureerd",
            "YouTube API": "niet geconfigureerd",
        }
        with patch.object(self.manager, "diagnostics", return_value=values):
            dialog = SettingsDialog(manager=self.manager)
            try:
                dialog.refresh_diagnostics()
                statuses = {
                    item.text() for item in dialog.diagnostic_status_labels.values()
                }
                self.assertTrue({"PASS", "WARNING", "ERROR"} <= statuses)
                self.assertEqual(dialog.diagnostic_labels["Spotify auth"].text(), "Niet geconfigureerd")
                self.assertEqual(dialog.diagnostic_labels["YouTube API"].text(), "Niet geconfigureerd")
                names = {
                    dialog.diagnostics_table.item(row, 0).text()
                    for row in range(dialog.diagnostics_table.rowCount())
                }
                self.assertIn("Spotify", names)
                self.assertIn("YouTube", names)
                self.assertNotIn("Spotify auth", names)
            finally:
                dialog.close()

    def test_constructs_quickly_without_external_calls(self):
        # Ruime CI-grens; de regressiebescherming zit primair in de call-asserties.
        started = time.monotonic()
        with patch.object(self.manager, "diagnostics") as diagnostics:
            dialog = SettingsDialog(manager=self.manager)
        elapsed = time.monotonic() - started
        try:
            diagnostics.assert_not_called()
            self.assertLess(elapsed, 2.0)
            self.assertFalse(hasattr(dialog, "worker"))
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
