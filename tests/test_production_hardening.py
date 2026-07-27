import json
import logging
import tempfile
import unittest
from pathlib import Path

from core.metadata.artwork import ArtworkCache
from core.reliability import CrashReporter, LoggingManager
from core.settings import SettingsManager
from database import SQLiteDatabase


class ProductionHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rotating_logging_contains_context_and_redacts_secrets(self):
        manager = LoggingManager(self.root / "logs", level="DEBUG", max_bytes=300, backup_count=2)
        manager.configure()
        logger = logging.getLogger("hardening.test")
        logger.error("api_key=super-secret")
        for index in range(30): logger.info("regel %s %s", index, "x" * 40)
        for handler in logging.getLogger().handlers: handler.flush()
        files = list((self.root / "logs").glob("megaman-recovery.log*"))
        self.assertGreaterEqual(len(files), 1)
        text = "".join(path.read_text(encoding="utf-8") for path in files)
        self.assertNotIn("super-secret", text)
        self.assertIn("hardening.test", text)
        manager.close()

    def test_crash_report_is_atomic_and_user_is_notified(self):
        notices = []
        reporter = CrashReporter(self.root / "crash", notices.append)
        try:
            raise RuntimeError("bewuste testcrash")
        except RuntimeError as error:
            target = reporter.report(type(error), error, error.__traceback__, "TestThread")
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["thread"], "TestThread")
        self.assertEqual(data["exception"], "RuntimeError")
        self.assertTrue(notices)
        self.assertFalse(list((self.root / "crash").glob("*.tmp")))

    def test_corrupt_settings_are_backed_up_and_recovered(self):
        path = self.root / "config" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text("{kapot", encoding="utf-8")
        manager = SettingsManager(path, environment={})
        from core.settings.defaults import CURRENT_VERSION
        self.assertEqual(manager.settings.version, CURRENT_VERSION)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["version"],
            CURRENT_VERSION,
        )
        self.assertEqual(len(list(path.parent.glob("settings.json.corrupt-*"))), 1)

    def test_database_transaction_rolls_back_and_integrity_is_ok(self):
        database = SQLiteDatabase(self.root / "db.sqlite")
        try:
            with self.assertRaises(RuntimeError):
                with database.transactie() as connection:
                    connection.execute(
                        "INSERT INTO mp3_bestanden(relatief_pad,bestand,bestaat,nul_bytes,rar_status,ffmpeg_status) VALUES('x','x',1,0,'OK','OK')"
                    )
                    raise RuntimeError("rollback")
            count = database.verbinding.execute(
                "SELECT COUNT(*) FROM mp3_bestanden WHERE relatief_pad='x'"
            ).fetchone()[0]
            self.assertEqual(count, 0)
            self.assertEqual(database.integrity_check(), (True, ("ok",)))
        finally:
            database.sluit()

    def test_large_queue_paginates_and_reports_full_statistics(self):
        database = SQLiteDatabase(self.root / "queue.sqlite")
        now = "2026-01-01T00:00:00"
        try:
            database.verbinding.executemany(
                """INSERT INTO recovery_items(id,rar_set_key,verwacht_rel_pad,
                verwacht_rel_pad_norm,probleem_type,probleem_bron,aangemaakt_op,
                bijgewerkt_op) VALUES(?,?,?,?,?,?,?,?)""",
                ((i, "set", f"{i}.mp3", f"{i}.mp3", "missing", "test", now, now)
                 for i in range(1, 1501)),
            )
            database.verbinding.executemany(
                """INSERT INTO download_queue(job_id,recovery_item_id,source_type,
                status,queue_position,created_at,updated_at,progress)
                VALUES(?,?,'YOUTUBE','WAITING',?,?,?,0)""",
                ((f"job-{i}", i, i, now, now) for i in range(1, 1501)),
            )
            database.verbinding.commit()
            from core.download_queue import DownloadQueueManager
            manager = DownloadQueueManager(database, settings_manager=SettingsManager(
                self.root / "settings.json", environment={}
            ))
            self.assertEqual(len(manager.jobs(limit=1000)), 1000)
            self.assertEqual(sum(1 for _ in manager.iter_jobs(257)), 1500)
            total, counts, progress = manager.queue_statistics()
            self.assertEqual((total, counts["WAITING"], progress), (1500, 1500, 0))
        finally:
            database.sluit()

    def test_cache_cleanup_removes_expired_and_excess_files_only(self):
        root = self.root / "cache"; root.mkdir()
        files = []
        for index in range(4):
            path = root / f"{index}.jpg"; path.write_bytes(b"x"); files.append(path)
            path.touch()
        cache = ArtworkCache(root, max_files=2, max_age_days=90)
        self.assertEqual(cache.cleanup(), 2)
        self.assertEqual(len(list(root.glob("*.jpg"))), 2)


if __name__ == "__main__":
    unittest.main()
