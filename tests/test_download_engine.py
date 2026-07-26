import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication

from core.download.engine import DownloadEngine
from core.download.errors import (
    DownloadCancelled, DownloadVerificationError, GeoBlockedError,
    NetworkDownloadError, PrivateVideoError, VideoRemovedError,
    classify_download_error,
)
from core.download.models import (
    DownloadProgress, PreparedDownload, ProviderDownloadResult,
)
from core.download.provider import YtDlpDownloadProvider
from core.download.worker import DownloadQueueWorker
from core.download_queue import (
    CANCELLED, COMPLETED, FAILED, WAITING, DownloadQueueManager,
)
from database import SQLiteDatabase
from gui.download_queue import DownloadQueueDialog


class FakeProvider:
    def __init__(self, payload=b"audio", error=None, progress=True, callback=None):
        self.payload = payload
        self.error = error
        self.progress = progress
        self.callback = callback
        self.cancelled = False
        self.cleaned = False

    def prepare(self, job, workspace):
        path = Path(workspace) / job.job_id
        path.mkdir(parents=True, exist_ok=True)
        return PreparedDownload(job.job_id, job.source_url, path, path / "source_audio.%(ext)s")

    def download(self, prepared, progress_callback=None):
        if self.error:
            raise self.error
        if self.cancelled:
            raise DownloadCancelled("gestopt")
        if progress_callback and self.progress:
            progress_callback(DownloadProgress(50, 2048, 3, 10, 5))
        if self.callback:
            self.callback()
        path = prepared.workspace / "source_audio.webm"
        path.write_bytes(self.payload)
        return ProviderDownloadResult(path, {})

    def cancel(self):
        self.cancelled = True

    def cleanup(self, prepared):
        self.cleaned = True
        for path in prepared.workspace.glob("*.part"):
            path.unlink()


class DownloadEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "queue.sqlite"
        self.db = SQLiteDatabase(self.path)
        now = "2026-07-26T00:00:00"
        self.db.verbinding.execute(
            "INSERT INTO recovery_sets(archive_name,archive_set_name,created_at,updated_at) VALUES(?,?,?,?)",
            ("Set.rar", "Set", now, now),
        )
        set_id = self.db.verbinding.execute("SELECT id FROM recovery_sets").fetchone()[0]
        self.items = []
        for index in range(2):
            item = self.db.verbinding.execute(
                """INSERT INTO recovery_items(
                rar_set_key,verwacht_rel_pad,verwacht_rel_pad_norm,probleem_type,
                probleem_bron,bepaalde_artiest,bepaalde_titel,aangemaakt_op,
                bijgewerkt_op,recovery_set_id,playlist_selected,
                youtube_review_status,selected_youtube_url)
                VALUES(?,?,?,?,?,?,?,?,?,?,1,'SELECTED',?)""",
                ("Set", f"{index}.mp3", f"{index}.mp3", "missing", "salvage",
                 "Artist", f"Title {index}", now, now, set_id,
                 f"https://youtube/{index}"),
            ).lastrowid
            candidate = self.db.verbinding.execute(
                """INSERT INTO youtube_candidates(
                recovery_item_id,video_id,youtube_url,title,confidence,
                artist_score,title_score,version_score,duration_score,
                channel_score,penalty_score,warnings_json,raw_metadata_json,
                search_query,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item, f"v{index}", f"https://youtube/{index}", f"Title {index}",
                 1, 1, 1, 1, 1, 1, 0, "[]", "{}", "query", now, now),
            ).lastrowid
            self.db.verbinding.execute(
                "UPDATE recovery_items SET selected_youtube_candidate_id=? WHERE id=?",
                (candidate, item),
            )
            self.items.append(item)
        self.db.verbinding.commit()
        self.manager = DownloadQueueManager(self.db)
        self.manager.enqueue()

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def test_download_start_progress_verify_en_database(self):
        logs, progress = [], []
        job = self.manager.dequeue()
        engine = DownloadEngine(
            self.manager, FakeProvider(), self.root / "downloads" / "temp",
            logs.append,
        )
        result = engine.execute(job, lambda _job, update: progress.append(update))
        stored = self.manager.get(job.job_id)
        self.assertEqual(stored.status, COMPLETED)
        self.assertEqual(stored.download_path, str(result.path))
        self.assertEqual(stored.download_size, 5)
        self.assertGreaterEqual(stored.download_duration, 0)
        self.assertEqual(progress[0].percent, 50)
        self.assertTrue(any("gestart" in line for line in logs))
        self.assertTrue(any("voltooid" in line for line in logs))
        row = self.db.verbinding.execute(
            "SELECT * FROM download_queue WHERE job_id=?", (job.job_id,)
        ).fetchone()
        self.assertEqual(row["download_status"], "COMPLETED")
        self.assertIsNotNone(row["download_started_at"])
        self.assertIsNotNone(row["download_finished_at"])

    def test_verify_weigert_ontbrekend_en_leeg_bestand(self):
        with self.assertRaises(DownloadVerificationError):
            DownloadEngine.verify(self.root / "missing")
        empty = self.root / "empty"
        empty.touch()
        with self.assertRaises(DownloadVerificationError):
            DownloadEngine.verify(empty)

    def test_downloadfout_wordt_opgeslagen(self):
        job = self.manager.dequeue()
        provider = FakeProvider(error=RuntimeError("Private video"))
        engine = DownloadEngine(self.manager, provider, self.root / "temp")
        with self.assertRaises(PrivateVideoError):
            engine.execute(job)
        stored = self.manager.get(job.job_id)
        self.assertEqual(stored.status, FAILED)
        self.assertEqual(stored.error_code, "VIDEO_PRIVATE")
        self.assertTrue(provider.cleaned)

    def test_annuleren_ruimt_partbestand_op(self):
        job = self.manager.dequeue()
        provider = FakeProvider()
        provider.cancel()
        engine = DownloadEngine(self.manager, provider, self.root / "temp")
        with self.assertRaises(DownloadCancelled):
            engine.execute(job)
        self.assertEqual(self.manager.get(job.job_id).status, CANCELLED)
        self.assertTrue(provider.cleaned)

    def test_foutclassificatie(self):
        self.assertIsInstance(classify_download_error(RuntimeError("video unavailable")), VideoRemovedError)
        self.assertIsInstance(classify_download_error(RuntimeError("geo blocked")), GeoBlockedError)
        self.assertIsInstance(classify_download_error(RuntimeError("network connection")), NetworkDownloadError)
        self.assertEqual(classify_download_error(TimeoutError()).code, "TIMEOUT")
        self.assertEqual(classify_download_error(OSError(28, "full")).code, "DISK_FULL")
        self.assertEqual(classify_download_error(RuntimeError("vreemd")).code, "YTDLP_ERROR")
        self.assertEqual(classify_download_error(KeyboardInterrupt()).code, "INTERRUPTED")

    def test_ytdlp_opties_zijn_audio_only_zonder_ffmpeg(self):
        captured = {}

        class Ydl:
            def __init__(self, options):
                captured.update(options)
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def extract_info(self, _url, download=True):
                path = self_path / "source_audio.webm"
                path.write_bytes(b"audio")
                return {"requested_downloads": [{"filepath": str(path)}]}

        self_path = self.root / "yt"
        self_path.mkdir()
        provider = YtDlpDownloadProvider(ydl_factory=Ydl)
        prepared = PreparedDownload("job", "https://youtube", self_path, self_path / "source_audio.%(ext)s")
        provider.download(prepared)
        self.assertEqual(captured["format"], "bestaudio/best")
        self.assertNotIn("postprocessors", captured)
        self.assertNotIn("ffmpeg_location", captured)

    def test_worker_thread_cleanup_en_pause_na_huidige_job(self):
        worker_ref = {}
        provider = FakeProvider(callback=lambda: worker_ref["worker"].request_pause())
        worker = DownloadQueueWorker(
            self.path, self.root / "temp", provider_factory=lambda: provider
        )
        worker_ref["worker"] = worker
        thread = QThread()
        worker.moveToThread(thread)
        loop = QEventLoop()
        completed = []
        worker.job_completed.connect(completed.append)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.finished.connect(loop.quit)
        thread.start()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        thread.wait(1000)
        self.assertFalse(thread.isRunning())
        self.assertEqual(len(completed), 1)
        statuses = [job.status for job in DownloadQueueManager(self.db).jobs()]
        self.assertIn(COMPLETED, statuses)
        self.assertIn(WAITING, statuses)

    def test_gui_toont_live_downloadprogress_en_ruimt_thread_op(self):
        factory = lambda db_path, temp_root: DownloadQueueWorker(
            db_path, temp_root, provider_factory=lambda: FakeProvider()
        )
        dialog = DownloadQueueDialog(
            database_path=self.path, worker_factory=factory,
            temp_root=self.root / "downloads" / "temp",
        )
        try:
            dialog.start_queue()
            loop = QEventLoop()
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(
                lambda: loop.quit() if dialog.worker_thread is None else None
            )
            timer.start()
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            timer.stop()
            self.assertIsNone(dialog.worker_thread)
            self.assertEqual(dialog.total_progress.value(), 100)
            self.assertIn("Completed: 2", dialog.summary_label.text())
            self.assertEqual(dialog.table.item(0, 5).text(), "100%")
            self.assertTrue(dialog.buttons["Start Queue"].isEnabled())
        finally:
            dialog.reject()


if __name__ == "__main__":
    unittest.main()
