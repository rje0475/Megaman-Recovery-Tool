import json
import os
import tempfile
import unittest
from pathlib import Path

from core.rar_cleanup import (
    hervat_rar_cleanups,
    registreer_rar_cleanup,
    voer_rar_cleanup_uit,
)
from core.settings import SettingsManager
from database import SQLiteDatabase, maak_database


class RarCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "cleanup.db"
        self.db = maak_database(self.db_path)
        self.run_id = self._run()

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def _run(self, key="set"):
        cursor = self.db.verbinding.execute(
            """
            INSERT INTO salvage_runs (
              started_at, finished_at, rar_set_key, source_status,
              winrar_result, sevenzip_result, chosen_archive,
              recovery_workspace, extraction_dir, expected_count, ok_count,
              missing_count, zero_byte_count, unreadable_count,
              size_mismatch_count, extra_count, recovery_item_count,
              final_status, summary
            ) VALUES ('start','finish',?,'COMPLETE','SUCCESS','SUCCESS',
                      'set.part01.rar','workspace','extracted',1,1,0,0,0,0,0,0,
                      'COMPLETE','ok')
            """, (key,),
        )
        self.db.verbinding.commit()
        return cursor.lastrowid

    def _register(self, volumes, enabled=True, eligible=True, run_id=None):
        return registreer_rar_cleanup(
            self.db, run_id or self.run_id, "set", self.root, volumes,
            enabled, eligible, "alle controles PASS" if eligible else "FAIL",
        )

    def test_success_single_volume_is_removed(self):
        archive = self.root / "Album.rar"
        archive.write_bytes(b"rar")
        result = voer_rar_cleanup_uit(self.db, self._register((archive,)))
        self.assertEqual(result.status, "removed")
        self.assertFalse(archive.exists())

    def test_multipart_removes_only_registered_volumes(self):
        volumes = tuple(self.root / f"Album.part{i:02d}.rar" for i in range(1, 4))
        for volume in volumes:
            volume.write_bytes(b"rar")
        other = self.root / "Andere.part01.rar"
        other.write_bytes(b"other")
        note = self.root / "Album.zip"
        note.write_bytes(b"zip")
        result = voer_rar_cleanup_uit(self.db, self._register(volumes))
        self.assertEqual(result.status, "removed")
        self.assertTrue(all(not volume.exists() for volume in volumes))
        self.assertEqual(other.read_bytes(), b"other")
        self.assertEqual(note.read_bytes(), b"zip")

    def test_disabled_or_failed_validation_retains_originals(self):
        for enabled, eligible in ((False, True), (True, False)):
            archive = self.root / f"keep-{enabled}-{eligible}.rar"
            archive.write_bytes(b"original")
            run_id = self._run(f"set-{enabled}-{eligible}")
            cleanup_id = self._register(
                (archive,), enabled=enabled, eligible=eligible, run_id=run_id
            )
            result = voer_rar_cleanup_uit(self.db, cleanup_id)
            self.assertIn(result.status, {"cleanup_disabled", "retained"})
            self.assertEqual(archive.read_bytes(), b"original")

    def test_partial_failure_is_recorded_and_retry_is_idempotent(self):
        volumes = tuple(self.root / f"Album.part{i:02d}.rar" for i in range(1, 4))
        for volume in volumes:
            volume.write_bytes(b"rar")
        cleanup_id = self._register(volumes)

        def fail_second(path):
            if path == volumes[1]:
                raise PermissionError("in gebruik")
            path.unlink()

        first = voer_rar_cleanup_uit(self.db, cleanup_id, remover=fail_second)
        self.assertEqual(first.status, "partial_cleanup")
        self.assertFalse(volumes[0].exists())
        self.assertTrue(volumes[1].exists())
        self.assertTrue(volumes[2].exists())
        row = self.db.verbinding.execute(
            "SELECT * FROM rar_cleanup_runs WHERE id=?", (cleanup_id,)
        ).fetchone()
        self.assertEqual(row["status"], "partial_cleanup")
        self.assertIn(str(volumes[1]), json.loads(row["failed_volumes"]))

        second = voer_rar_cleanup_uit(self.db, cleanup_id)
        self.assertEqual(second.status, "removed")
        self.assertTrue(all(not volume.exists() for volume in volumes))

    def test_pending_cleanup_is_resumed_after_restart(self):
        archive = self.root / "Restart.part01.rar"
        archive.write_bytes(b"rar")
        cleanup_id = self._register((archive,))
        self.db.sluit()
        self.db = SQLiteDatabase(self.db_path)
        results = hervat_rar_cleanups(self.db)
        self.assertEqual([result.cleanup_id for result in results], [cleanup_id])
        self.assertEqual(results[0].status, "removed")
        self.assertFalse(archive.exists())

    def test_symlink_and_outside_path_are_never_removed(self):
        outside_dir = self.root.parent / f"{self.root.name}-outside"
        outside_dir.mkdir(exist_ok=True)
        outside = outside_dir / "Outside.rar"
        outside.write_bytes(b"outside")
        try:
            cleanup_id = self._register((outside,))
            result = voer_rar_cleanup_uit(self.db, cleanup_id)
            self.assertEqual(result.status, "partial_cleanup")
            self.assertEqual(outside.read_bytes(), b"outside")

            if hasattr(os, "symlink"):
                link = self.root / "Link.part01.rar"
                try:
                    link.symlink_to(outside)
                except OSError:
                    return
                run_id = self._run("symlink")
                link_cleanup = self._register((link,), run_id=run_id)
                linked = voer_rar_cleanup_uit(self.db, link_cleanup)
                self.assertEqual(linked.status, "partial_cleanup")
                self.assertTrue(link.is_symlink())
                self.assertEqual(outside.read_bytes(), b"outside")
        finally:
            outside.unlink(missing_ok=True)
            outside_dir.rmdir()

    def test_setting_defaults_true_and_persists_false(self):
        settings_path = self.root / "config" / "settings.json"
        manager = SettingsManager(settings_path, environment={})
        self.assertTrue(
            manager.section("general")["delete_original_rars_after_success"]
        )
        manager.update_section(
            "general", {"delete_original_rars_after_success": False}
        )
        reloaded = SettingsManager(settings_path, environment={})
        self.assertFalse(
            reloaded.section("general")["delete_original_rars_after_success"]
        )


if __name__ == "__main__":
    unittest.main()
