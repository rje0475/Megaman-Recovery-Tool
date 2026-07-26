import json
import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
