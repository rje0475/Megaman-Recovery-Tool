"""Persistente downloadqueue-infrastructuur zonder downloaduitvoerder."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


WAITING = "WAITING"
QUEUED = "QUEUED"
PREPARING = "PREPARING"
RUNNING = "RUNNING"
DOWNLOADING = "DOWNLOADING"
VERIFYING = "VERIFYING"
DOWNLOADED = "DOWNLOADED"
PROCESSING = "PROCESSING"
VALIDATING = "VALIDATING"
PROCESSED = "PROCESSED"
FINALIZING = "FINALIZING"
RECOVERED = "RECOVERED"
PAUSED = "PAUSED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
SKIPPED = "SKIPPED"
STATUSES = frozenset({
    WAITING, QUEUED, PREPARING, RUNNING, PAUSED,
    DOWNLOADING, VERIFYING, DOWNLOADED, PROCESSING, VALIDATING, PROCESSED,
    FINALIZING, RECOVERED, COMPLETED, FAILED, CANCELLED, SKIPPED,
})
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadJob:
    job_id: str
    recovery_item_id: int
    queue_position: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    retry_count: int
    max_retries: int
    progress: int
    current_stage: str | None
    error_code: str | None
    error_message: str | None
    status: str
    source_type: str
    artist: str = ""
    title: str = ""
    source_url: str | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: int | None = None
    download_size: int | None = None
    download_path: str | None = None
    download_duration: float | None = None
    processed_path: str | None = None
    processed_size: int | None = None
    processed_format: str | None = None
    processed_codec: str | None = None
    processed_bitrate: int | None = None
    processed_sample_rate: int | None = None
    processed_channels: int | None = None
    processed_duration: float | None = None
    processing_status: str | None = None
    processing_warning: str | None = None
    source_duration: float | None = None
    source_format: str | None = None
    source_removed: bool = False
    final_path: str | None = None
    filename: str | None = None
    metadata_written: bool = False
    artwork_written: bool = False
    finalization_status: str | None = None
    final_size: int | None = None


class DownloadQueueManager:
    """Beheert alleen records en statusovergangen; voert niets uit."""

    def __init__(self, database, log_callback=None, settings_manager=None):
        from core.settings import get_settings_manager
        self.database = database
        self.log_callback = log_callback or LOGGER.info
        self.settings_manager = settings_manager or get_settings_manager()
        self._herstel_onafgeronde_jobs()

    def _now(self):
        return datetime.now().isoformat(timespec="seconds")

    def _herstel_onafgeronde_jobs(self):
        now = self._now()
        rows = self.database.verbinding.execute(
            """SELECT job_id,status FROM download_queue
            WHERE status IN ('RUNNING','DOWNLOADING','VERIFYING','PREPARING','QUEUED')"""
        ).fetchall()
        for row in rows:
            self.database.verbinding.execute(
                """UPDATE download_queue SET status='WAITING', progress=0,
                last_stage='Hersteld na applicatiestop', updated_at=?
                WHERE job_id=?""", (now, row["job_id"])
            )
            self._log(row["job_id"], row["status"], "WAITING")
        self.database.verbinding.commit()
        rows = self.database.verbinding.execute(
            """SELECT job_id,status,download_path,processed_path,processing_status
            FROM download_queue WHERE status IN ('PROCESSING','VALIDATING','FINALIZING','COMPLETED')"""
        ).fetchall()
        for row in rows:
            processed = Path(row["processed_path"]) if row["processed_path"] else None
            source = Path(row["download_path"]) if row["download_path"] else None
            if (
                row["processing_status"] == PROCESSED
                and processed and processed.is_file() and processed.stat().st_size > 0
            ):
                status = PROCESSED
            elif source and source.is_file() and source.stat().st_size > 0:
                status = DOWNLOADED
            else:
                status = WAITING
            self.database.verbinding.execute(
                "UPDATE download_queue SET status=?,updated_at=? WHERE job_id=?",
                (status, now, row["job_id"]),
            )
        self.database.verbinding.commit()

    def enqueue(self, recovery_item_ids=None):
        parameters = []
        filter_sql = ""
        if recovery_item_ids is not None:
            ids = tuple(dict.fromkeys(int(value) for value in recovery_item_ids))
            if not ids:
                return ()
            filter_sql = f" AND r.id IN ({','.join('?' for _ in ids)})"
            parameters.extend(ids)
        rows = self.database.verbinding.execute(
            """
            SELECT r.id, r.selected_youtube_candidate_id,
                   r.selected_spotify_candidate_id
            FROM recovery_items r
            WHERE r.playlist_selected=1
              AND r.selected_youtube_candidate_id IS NOT NULL
              AND COALESCE(r.selected_youtube_url, '') <> ''
              AND r.youtube_review_status='SELECTED'
            """ + filter_sql + " ORDER BY r.id",
            parameters,
        ).fetchall()
        next_position = self.database.verbinding.execute(
            "SELECT COALESCE(MAX(queue_position),0)+1 FROM download_queue"
        ).fetchone()[0]
        created = []
        now = self._now()
        for row in rows:
            existing = self.database.verbinding.execute(
                "SELECT job_id FROM download_queue WHERE recovery_item_id=?",
                (row["id"],),
            ).fetchone()
            if existing:
                continue
            job_id = str(uuid.uuid4())
            self.database.verbinding.execute(
                """INSERT INTO download_queue(
                job_id,recovery_item_id,youtube_candidate_id,
                spotify_candidate_id,source_type,status,priority,
                queue_position,retries,max_retries,progress,last_stage,
                created_at,updated_at)
                VALUES(?,?,?,?,?,'WAITING',0,?,?,?,0,'Wachten',?,?)""",
                (job_id, row["id"], row["selected_youtube_candidate_id"],
                 row["selected_spotify_candidate_id"], "YOUTUBE",
                 next_position, 0,
                 int(self.settings_manager.section("download")["retries"]), now, now),
            )
            created.append(job_id)
            next_position += 1
        self.database.verbinding.commit()
        return tuple(created)

    def jobs(self):
        rows = self.database.verbinding.execute(
            """SELECT q.*,r.bepaalde_artiest,r.bepaalde_titel,
                      r.selected_youtube_url
            FROM download_queue q JOIN recovery_items r
              ON r.id=q.recovery_item_id
            ORDER BY q.queue_position,q.created_at"""
        ).fetchall()
        return tuple(self._job(row) for row in rows)

    def get(self, job_id):
        return next((job for job in self.jobs() if job.job_id == job_id), None)

    def dequeue(self):
        row = self.database.verbinding.execute(
            """SELECT job_id FROM download_queue
            WHERE status IN ('WAITING','DOWNLOADED')
            ORDER BY priority DESC,queue_position LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        self.transition(row["job_id"], QUEUED, stage="In wachtrij", progress=0)
        return self.get(row["job_id"])

    def transition(
        self, job_id, status, stage=None, progress=None,
        error_code=None, error_message=None,
    ):
        if status not in STATUSES:
            raise ValueError(f"Onbekende downloadstatus: {status}")
        row = self.database.verbinding.execute(
            "SELECT * FROM download_queue WHERE job_id=?", (job_id,)
        ).fetchone()
        if not row:
            raise ValueError("Downloadjob bestaat niet.")
        now = self._now()
        started = row["started_at"]
        finished = row["finished_at"]
        if status in {RUNNING, DOWNLOADING} and not started:
            started = now
        if status in {RECOVERED, COMPLETED, FAILED, CANCELLED, SKIPPED}:
            finished = now
        if progress is None:
            progress = 100 if status in {RECOVERED, COMPLETED} else row["progress"]
        self.database.verbinding.execute(
            """UPDATE download_queue SET status=?,progress=?,last_stage=?,
            error_code=?,last_error=?,started_at=?,finished_at=?,updated_at=?
            WHERE job_id=?""",
            (status, max(0, min(100, int(progress))), stage,
             error_code, error_message, started, finished, now, job_id),
        )
        if status in {DOWNLOADING, VERIFYING, DOWNLOADED, COMPLETED, FAILED, CANCELLED}:
            self.database.verbinding.execute(
                """UPDATE download_queue SET download_status=?,
                download_started_at=CASE WHEN ?='DOWNLOADING'
                    THEN COALESCE(download_started_at,?) ELSE download_started_at END,
                download_finished_at=CASE WHEN ? IN ('DOWNLOADED','COMPLETED','FAILED','CANCELLED')
                    THEN COALESCE(download_finished_at,?) ELSE download_finished_at END
                WHERE job_id=?""",
                (status, status, now, status, now, job_id),
            )
        self.database.verbinding.commit()
        self._log(job_id, row["status"], status)
        return self.get(job_id)

    def cancel(self, job_id):
        return self.transition(job_id, CANCELLED, stage="Geannuleerd")

    def update_download_progress(self, job_id, update):
        self.database.verbinding.execute(
            """UPDATE download_queue SET progress=?,download_speed=?,
            download_eta=?,download_size=COALESCE(?,download_size),
            last_stage='Downloaden',updated_at=? WHERE job_id=?""",
            (max(0, min(100, round(update.percent))),
             update.speed_bytes_per_second, update.eta_seconds,
             update.total_bytes, self._now(), job_id),
        )
        self.database.verbinding.commit()

    def complete_download(self, job_id, path, size, duration):
        now = self._now()
        self.database.verbinding.execute(
            """UPDATE download_queue SET download_path=?,download_size=?,
            download_duration=?,download_started_at=COALESCE(download_started_at,started_at),
            download_finished_at=?,download_status='DOWNLOADED' WHERE job_id=?""",
            (str(path), int(size), float(duration), now, job_id),
        )
        self.database.verbinding.commit()
        return self.transition(job_id, DOWNLOADED, "Download voltooid", 100)

    def start_processing(self, job_id, source_duration=None):
        now = self._now()
        self.database.verbinding.execute(
            """UPDATE download_queue SET processing_started_at=COALESCE(
            processing_started_at,?),processing_status='PROCESSING',
            source_duration=COALESCE(?,source_duration) WHERE job_id=?""",
            (now, source_duration, job_id),
        )
        self.database.verbinding.commit()
        return self.transition(job_id, PROCESSING, "Audio verwerken", 0)

    def update_processing_progress(self, job_id, update):
        progress = 0 if update.percent is None else round(update.percent)
        self.database.verbinding.execute(
            """UPDATE download_queue SET progress=?,last_stage=?,updated_at=?
            WHERE job_id=?""",
            (max(0, min(100, progress)), update.stage, self._now(), job_id),
        )
        self.database.verbinding.commit()

    def start_validation(self, job_id):
        self.database.verbinding.execute(
            "UPDATE download_queue SET processing_status='VALIDATING' WHERE job_id=?",
            (job_id,),
        )
        self.database.verbinding.commit()
        return self.transition(job_id, VALIDATING, "Audio valideren", 99)

    def complete_processing(self, job_id, result):
        now = self._now()
        warning = "; ".join(result.validation.warnings) or None
        self.database.verbinding.execute(
            """UPDATE download_queue SET processed_path=?,processed_size=?,
            processed_format=?,processed_codec=?,processed_bitrate=?,
            processed_sample_rate=?,processed_channels=?,processed_duration=?,
            processing_finished_at=?,processing_status='PROCESSED',
            processing_error_code=NULL,processing_error_message=NULL,
            processing_warning=?,source_duration=?,source_format=? WHERE job_id=?""",
            (str(result.processed_path), result.size, result.format, result.codec,
             result.bitrate, result.sample_rate, result.channels, result.duration,
             now, warning, result.source_duration, result.source_format, job_id),
        )
        self.database.verbinding.commit()
        return self.transition(job_id, PROCESSED, "Verwerking voltooid", 100)

    def fail_processing(self, job_id, error):
        now = self._now()
        message = str(error)
        if getattr(error, "stderr", None):
            message = f"{message}\n{error.stderr}".strip()
        self.database.verbinding.execute(
            """UPDATE download_queue SET processing_finished_at=?,
            processing_status='FAILED',processing_error_code=?,
            processing_error_message=? WHERE job_id=?""",
            (now, error.code, message, job_id),
        )
        self.database.verbinding.commit()
        return self.transition(
            job_id, FAILED, "Audioverwerking mislukt",
            error_code=error.code, error_message=str(error),
        )

    def start_finalization(self, job_id):
        self.database.verbinding.execute(
            """UPDATE download_queue SET finalization_status='FINALIZING',
            finalization_error_code=NULL,finalization_error_message=NULL WHERE job_id=?""",
            (job_id,),
        )
        self.database.verbinding.commit()
        return self.transition(job_id, FINALIZING, "Metadata en eindlocatie", 0)

    def complete_finalization(self, job_id, result):
        now = self._now()
        self.database.verbinding.execute(
            """UPDATE download_queue SET final_path=?,filename=?,final_size=?,
            metadata_written=?,artwork_written=?,finalized_at=?,
            finalization_status='RECOVERED',written_tags=?,processed_path=NULL
            WHERE job_id=?""",
            (str(result.final_path), result.filename, result.size,
             int(result.metadata_written), int(result.artwork_written), now,
             ",".join(result.tags), job_id),
        )
        self.database.verbinding.execute(
            """UPDATE recovery_items SET geplaatst=1,download_verwerkt=1,
            bijgewerkt_op=? WHERE id=(SELECT recovery_item_id FROM download_queue
            WHERE job_id=?)""", (now, job_id),
        )
        self.database.verbinding.commit()
        return self.transition(job_id, RECOVERED, "Herstel voltooid", 100)

    def fail_finalization(self, job_id, error):
        self.database.verbinding.execute(
            """UPDATE download_queue SET finalization_status='FAILED',
            finalization_error_code=?,finalization_error_message=? WHERE job_id=?""",
            (error.code, str(error), job_id),
        )
        self.database.verbinding.commit()
        return self.transition(job_id, FAILED, "Finalisatie mislukt",
                               error_code=error.code, error_message=str(error))

    def mark_source_removed(self, job_id):
        self.database.verbinding.execute(
            "UPDATE download_queue SET source_removed=1,updated_at=? WHERE job_id=?",
            (self._now(), job_id),
        )
        self.database.verbinding.commit()

    def fail_download(self, job_id, code, message):
        now = self._now()
        self.database.verbinding.execute(
            """UPDATE download_queue SET download_finished_at=?,
            download_status='FAILED' WHERE job_id=?""", (now, job_id)
        )
        self.database.verbinding.commit()
        return self.transition(
            job_id, FAILED, "Download mislukt",
            error_code=code, error_message=message,
        )

    def pause(self, job_id=None):
        ids = self._ids(job_id, {WAITING, QUEUED, PREPARING, RUNNING})
        return tuple(self.transition(value, PAUSED, stage="Gepauzeerd") for value in ids)

    def resume(self, job_id=None):
        ids = self._ids(job_id, {PAUSED})
        return tuple(self.transition(value, WAITING, stage="Wachten", progress=0) for value in ids)

    def retry_failed(self):
        rows = self.database.verbinding.execute(
            "SELECT job_id,retries,max_retries FROM download_queue WHERE status='FAILED'"
        ).fetchall()
        retried = []
        for row in rows:
            if row["retries"] >= row["max_retries"]:
                continue
            self.database.verbinding.execute(
                "UPDATE download_queue SET retries=retries+1 WHERE job_id=?",
                (row["job_id"],),
            )
            retried.append(self.transition(
                row["job_id"], WAITING, stage="Opnieuw ingepland",
                progress=0, error_code=None, error_message=None,
            ))
        return tuple(retried)

    def clear_completed(self):
        cursor = self.database.verbinding.execute(
            "DELETE FROM download_queue WHERE status IN ('COMPLETED','RECOVERED')"
        )
        self.database.verbinding.commit()
        self._renumber()
        return cursor.rowcount

    def move_up(self, job_id):
        return self._move(job_id, -1)

    def move_down(self, job_id):
        return self._move(job_id, 1)

    def _move(self, job_id, direction):
        jobs = list(self.jobs())
        index = next((i for i, job in enumerate(jobs) if job.job_id == job_id), None)
        target = None if index is None else index + direction
        if index is None or target < 0 or target >= len(jobs):
            return False
        first, second = jobs[index], jobs[target]
        self.database.verbinding.execute(
            "UPDATE download_queue SET queue_position=? WHERE job_id=?",
            (-1, first.job_id),
        )
        self.database.verbinding.execute(
            "UPDATE download_queue SET queue_position=? WHERE job_id=?",
            (first.queue_position, second.job_id),
        )
        self.database.verbinding.execute(
            "UPDATE download_queue SET queue_position=? WHERE job_id=?",
            (second.queue_position, first.job_id),
        )
        self.database.verbinding.commit()
        return True

    def remove_selected(self, job_ids):
        return tuple(self.cancel(job_id) for job_id in job_ids)

    def _ids(self, job_id, statuses):
        if job_id:
            row = self.database.verbinding.execute(
                "SELECT status FROM download_queue WHERE job_id=?", (job_id,)
            ).fetchone()
            return (job_id,) if row and row["status"] in statuses else ()
        placeholders = ",".join("?" for _ in statuses)
        return tuple(row[0] for row in self.database.verbinding.execute(
            f"SELECT job_id FROM download_queue WHERE status IN ({placeholders})",
            tuple(statuses),
        ))

    def _renumber(self):
        for position, job in enumerate(self.jobs(), 1):
            self.database.verbinding.execute(
                "UPDATE download_queue SET queue_position=? WHERE job_id=?",
                (position, job.job_id),
            )
        self.database.verbinding.commit()

    def _log(self, job_id, old, new):
        self.log_callback(f"Downloadjob {job_id}: {old} -> {new}")

    @staticmethod
    def _job(row):
        return DownloadJob(
            job_id=row["job_id"], recovery_item_id=row["recovery_item_id"],
            queue_position=row["queue_position"], created_at=row["created_at"],
            started_at=row["started_at"], finished_at=row["finished_at"],
            retry_count=row["retries"], max_retries=row["max_retries"],
            progress=row["progress"], current_stage=row["last_stage"],
            error_code=row["error_code"], error_message=row["last_error"],
            status=row["status"], source_type=row["source_type"],
            artist=row["bepaalde_artiest"] or "", title=row["bepaalde_titel"] or "",
            source_url=row["selected_youtube_url"],
            speed_bytes_per_second=row["download_speed"],
            eta_seconds=row["download_eta"], download_size=row["download_size"],
            download_path=row["download_path"],
            download_duration=row["download_duration"],
            processed_path=row["processed_path"],
            processed_size=row["processed_size"],
            processed_format=row["processed_format"],
            processed_codec=row["processed_codec"],
            processed_bitrate=row["processed_bitrate"],
            processed_sample_rate=row["processed_sample_rate"],
            processed_channels=row["processed_channels"],
            processed_duration=row["processed_duration"],
            processing_status=row["processing_status"],
            processing_warning=row["processing_warning"],
            source_duration=row["source_duration"],
            source_format=row["source_format"],
            source_removed=bool(row["source_removed"]),
            final_path=row["final_path"], filename=row["filename"],
            metadata_written=bool(row["metadata_written"]),
            artwork_written=bool(row["artwork_written"]),
            finalization_status=row["finalization_status"],
            final_size=row["final_size"],
        )
