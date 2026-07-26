import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from core.download_queue import (
    CANCELLED, COMPLETED, FAILED, PAUSED, PREPARING, QUEUED, RUNNING,
    WAITING, DownloadQueueManager,
)
from database import SQLiteDatabase
from gui.download_queue import DownloadQueueDialog, DownloadQueueSimulator
from report import maak_rapport


class DownloadQueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "queue.sqlite"
        self.db = SQLiteDatabase(self.path)
        now = "2026-07-26T00:00:00"
        self.db.verbinding.execute(
            "INSERT INTO recovery_sets(archive_name,archive_set_name,created_at,updated_at) VALUES(?,?,?,?)",
            ("Collectie.part01.rar", "Collectie", now, now),
        )
        set_id = self.db.verbinding.execute("SELECT id FROM recovery_sets").fetchone()[0]
        self.item_ids = []
        for index in range(3):
            cursor = self.db.verbinding.execute(
                """INSERT INTO recovery_items(
                rar_set_key,verwacht_rel_pad,verwacht_rel_pad_norm,
                probleem_type,probleem_bron,bepaalde_artiest,bepaalde_titel,
                aangemaakt_op,bijgewerkt_op,recovery_set_id,playlist_selected,
                youtube_review_status,selected_youtube_url)
                VALUES(?,?,?,?,?,?,?,?,?,?,1,'SELECTED',?)""",
                ("Collectie", f"Track {index}.mp3", f"track {index}.mp3",
                 "missing", "salvage", f"Artist {index}", f"Title {index}",
                 now, now, set_id, f"https://youtube/{index}"),
            )
            item_id = cursor.lastrowid
            candidate = self.db.verbinding.execute(
                """INSERT INTO youtube_candidates(
                recovery_item_id,video_id,youtube_url,title,channel_name,
                confidence,artist_score,title_score,version_score,duration_score,
                channel_score,penalty_score,warnings_json,raw_metadata_json,
                search_query,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, f"video{index}", f"https://youtube/{index}",
                 f"Title {index}", f"Artist {index} - Topic", .98, 1, 1, 1,
                 1, 1, 0, "[]", "{}", "query", now, now),
            ).lastrowid
            self.db.verbinding.execute(
                "UPDATE recovery_items SET selected_youtube_candidate_id=? WHERE id=?",
                (candidate, item_id),
            )
            self.item_ids.append(item_id)
        self.db.verbinding.commit()
        self.logs = []
        self.manager = DownloadQueueManager(self.db, self.logs.append)

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def test_enqueue_en_geen_duplicaten(self):
        first = self.manager.enqueue()
        second = self.manager.enqueue()
        self.assertEqual(len(first), 3)
        self.assertEqual(second, ())
        self.assertEqual([job.queue_position for job in self.manager.jobs()], [1, 2, 3])

    def test_alleen_gereviewde_geselecteerde_youtube_items(self):
        self.db.verbinding.execute(
            "UPDATE recovery_items SET playlist_selected=0 WHERE id=?",
            (self.item_ids[0],),
        )
        self.db.verbinding.execute(
            "UPDATE recovery_items SET youtube_review_status='REVIEWED_NONE' WHERE id=?",
            (self.item_ids[1],),
        )
        self.db.verbinding.commit()
        created = self.manager.enqueue()
        self.assertEqual(len(created), 1)
        self.assertEqual(self.manager.jobs()[0].recovery_item_id, self.item_ids[2])

    def test_dequeue_en_statuslogging(self):
        self.manager.enqueue()
        job = self.manager.dequeue()
        self.assertEqual(job.status, QUEUED)
        self.manager.transition(job.job_id, PREPARING, "Voorbereiden", 10)
        self.manager.transition(job.job_id, RUNNING, "Uitvoeren", 25)
        self.manager.transition(job.job_id, COMPLETED, "Klaar", 100)
        self.assertTrue(any("WAITING -> QUEUED" in line for line in self.logs))
        self.assertTrue(any("RUNNING -> COMPLETED" in line for line in self.logs))

    def test_move_up_en_down(self):
        self.manager.enqueue()
        jobs = self.manager.jobs()
        self.assertTrue(self.manager.move_up(jobs[1].job_id))
        self.assertEqual(self.manager.jobs()[0].job_id, jobs[1].job_id)
        self.assertTrue(self.manager.move_down(jobs[1].job_id))
        self.assertEqual(self.manager.jobs()[1].job_id, jobs[1].job_id)

    def test_pause_resume_cancel(self):
        self.manager.enqueue()
        first = self.manager.jobs()[0]
        self.manager.pause(first.job_id)
        self.assertEqual(self.manager.get(first.job_id).status, PAUSED)
        self.manager.resume(first.job_id)
        self.assertEqual(self.manager.get(first.job_id).status, WAITING)
        self.manager.cancel(first.job_id)
        self.assertEqual(self.manager.get(first.job_id).status, CANCELLED)

    def test_retry_failed_en_max_retries(self):
        self.manager.enqueue()
        first = self.manager.jobs()[0]
        self.manager.transition(first.job_id, FAILED, error_code="SIM", error_message="fout")
        retried = self.manager.retry_failed()
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0].status, WAITING)
        self.assertEqual(retried[0].retry_count, 1)

    def test_clear_completed(self):
        self.manager.enqueue()
        first = self.manager.jobs()[0]
        self.manager.transition(first.job_id, COMPLETED)
        self.assertEqual(self.manager.clear_completed(), 1)
        self.assertEqual(len(self.manager.jobs()), 2)

    def test_running_wordt_na_herstart_waiting(self):
        self.manager.enqueue()
        first = self.manager.jobs()[0]
        self.manager.transition(first.job_id, RUNNING, progress=50)
        restored = DownloadQueueManager(self.db)
        self.assertEqual(restored.get(first.job_id).status, WAITING)
        self.assertEqual(restored.get(first.job_id).progress, 0)

    def test_simulator_doorloopt_statussen_zonder_thread(self):
        self.manager.enqueue([self.item_ids[0]])
        simulator = DownloadQueueSimulator(self.manager, interval_ms=1)
        loop = QEventLoop()
        simulator.completed.connect(loop.quit)
        simulator.start()
        QTimer.singleShot(2000, loop.quit)
        loop.exec()
        self.assertFalse(simulator.active)
        self.assertEqual(self.manager.jobs()[0].status, COMPLETED)
        self.assertEqual(self.manager.jobs()[0].progress, 100)

    def test_gui_queue_en_timercleanup(self):
        self.manager.enqueue()
        dialog = DownloadQueueDialog(
            database_path=self.path, simulator_interval_ms=1
        )
        try:
            self.assertEqual(dialog.table.rowCount(), 3)
            self.assertIn("Totale queue: 3", dialog.summary_label.text())
            self.assertIn("Onbewerkte bronaudio", dialog.notice.text())
            dialog.simulator.start()
            self.assertTrue(dialog.simulator.active)
            dialog.reject()
            self.assertFalse(dialog.simulator.active)
        finally:
            if dialog.isVisible():
                dialog.reject()

    def test_migratie_en_geen_downloadoppervlak(self):
        columns = {
            row["name"] for row in self.db.verbinding.execute(
                "PRAGMA table_info(download_queue)"
            )
        }
        self.assertTrue({"job_id", "status", "queue_position", "last_error"} <= columns)
        self.assertFalse(hasattr(self.manager, "download"))
        self.assertFalse(hasattr(self.manager, "execute"))

    def test_rapport_bevat_queuevolgorde_status_retries_en_fouten(self):
        self.manager.enqueue([self.item_ids[0]])
        job = self.manager.jobs()[0]
        self.manager.transition(
            job.job_id, FAILED, error_code="SIM", error_message="testfout"
        )
        report_path = maak_rapport(self.temp.name, self.db)
        text = report_path.read_text(encoding="utf-8")
        self.assertIn("Download Queue", text)
        self.assertIn("Aantal jobs: 1", text)
        self.assertIn("status=FAILED", text)
        self.assertIn("retries=0/3", text)
        self.assertIn("fout=testfout", text)
        self.assertIn("downloadstatus=FAILED", text)
        self.assertIn("processingstatus=—", text)
        self.assertIn("processed=—", text)


if __name__ == "__main__":
    unittest.main()
