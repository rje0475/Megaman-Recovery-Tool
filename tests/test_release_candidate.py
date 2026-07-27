import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from PySide6.QtWidgets import QApplication

from cli import main
from core.release import BackupError, ProjectBackupManager, run_health_check, run_self_test
from core.settings import SettingsManager
from core.version import VersionInfo, __version__
from database import SQLiteDatabase
from gui.release_dialogs import AboutDialog, HealthCheckDialog


class ReleaseCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = SettingsManager(self.root / "config/settings.json", environment={})
        database = SQLiteDatabase(self.root / "megaman_recovery.db")
        database.verbinding.execute(
            "INSERT INTO mp3_bestanden(relatief_pad,bestand,bestaat,nul_bytes,rar_status,ffmpeg_status) VALUES('x','x',1,0,'OK','OK')"
        )
        database.verbinding.commit(); database.sluit()

    def tearDown(self):
        self.temp.cleanup()

    def test_version_info_and_cli_version(self):
        self.assertEqual(__version__, "1.0.0")
        info = VersionInfo.current()
        self.assertIn("1.0.0", info.display)
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("1.0.0", output.getvalue())

    def test_backup_contains_only_allowlisted_project_data(self):
        (self.root / "downloads").mkdir(); (self.root / "downloads/audio.mp3").write_bytes(b"audio")
        (self.root / "logs").mkdir(); (self.root / "logs/app.log").write_text("log", encoding="utf-8")
        manager = ProjectBackupManager(self.root)
        backup = manager.create(self.root / "backup.zip", include_logs=True)
        with zipfile.ZipFile(backup.path) as archive:
            names = set(archive.namelist())
        self.assertIn("database/megaman_recovery.db", names)
        self.assertIn("config/settings.json", names)
        self.assertIn("logs/app.log", names)
        self.assertFalse(any("download" in name for name in names))

    def test_restore_is_atomic_and_keeps_pre_restore_copy(self):
        manager = ProjectBackupManager(self.root)
        backup = manager.create(self.root / "backup.zip")
        original_settings = json.loads((self.root / "config/settings.json").read_text(encoding="utf-8"))
        self.settings.update_section("general", {"theme": "dark"})
        restored = manager.restore(backup.path)
        self.assertIn("config/settings.json", restored)
        current = json.loads((self.root / "config/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(current["general"]["theme"], original_settings["general"]["theme"])
        self.assertTrue(list((self.root / "config").glob("settings.json.before-restore-*")))

    def test_restore_rejects_zip_slip(self):
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as value: value.writestr("../outside.txt", "bad")
        with self.assertRaises(BackupError):
            ProjectBackupManager(self.root).restore(archive)
        self.assertFalse((self.root.parent / "outside.txt").exists())

    def test_health_check_and_self_test(self):
        health = run_health_check(self.root / "megaman_recovery.db", self.settings)
        self.assertIn(health.status, {"PASS", "WARNING"})
        self.assertTrue(any(item.name == "Database" and item.status == "PASS" for item in health.items))
        self_test = run_self_test(self.root / "megaman_recovery.db", self.settings)
        self.assertTrue({"Queue", "Audio", "Metadata", "Logging"} <= {item.name for item in self_test.items})

    def test_about_and_health_dialogs_offscreen(self):
        about = AboutDialog(settings_manager=self.settings)
        health = HealthCheckDialog(runner=lambda: run_health_check(
            self.root / "megaman_recovery.db", self.settings
        ))
        try:
            self.assertIn("About", about.windowTitle())
            self.assertIn(health.findChild(type(health.layout().itemAt(0).widget()), "healthStatus").text().split()[0], {"Eindstatus:"})
        finally:
            about.close(); health.close()

    def test_distribution_files_exist(self):
        project = Path(__file__).resolve().parents[1]
        for name in ("LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
                     "megaman_recovery.spec", "requirements-build.txt",
                     ".github/workflows/ci.yml", ".github/PULL_REQUEST_TEMPLATE.md",
                     ".github/ISSUE_TEMPLATE/bug_report.yml",
                     ".github/ISSUE_TEMPLATE/feature_request.yml"):
            self.assertTrue((project / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
