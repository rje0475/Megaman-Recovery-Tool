"""Persistente downloadqueue-infrastructuur zonder downloaduitvoerder."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime


WAITING = "WAITING"
QUEUED = "QUEUED"
PREPARING = "PREPARING"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
SKIPPED = "SKIPPED"
STATUSES = frozenset({
    WAITING, QUEUED, PREPARING, RUNNING, PAUSED,
    COMPLETED, FAILED, CANCELLED, SKIPPED,
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


class DownloadQueueManager:
    """Beheert alleen records en statusovergangen; voert niets uit."""

    def __init__(self, database, log_callback=None):
        self.database = database
        self.log_callback = log_callback or LOGGER.info
        self._herstel_onafgeronde_jobs()

    def _now(self):
        return datetime.now().isoformat(timespec="seconds")

    def _herstel_onafgeronde_jobs(self):
        now = self._now()
        rows = self.database.verbinding.execute(
            "SELECT job_id,status FROM download_queue WHERE status='RUNNING'"
        ).fetchall()
        for row in rows:
            self.database.verbinding.execute(
                """UPDATE download_queue SET status='WAITING', progress=0,
                last_stage='Hersteld na applicatiestop', updated_at=?
                WHERE job_id=?""", (now, row["job_id"])
            )
            self._log(row["job_id"], "RUNNING", "WAITING")
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
                VALUES(?,?,?,?,?,'WAITING',0,?,?,3,0,'Wachten',?,?)""",
                (job_id, row["id"], row["selected_youtube_candidate_id"],
                 row["selected_spotify_candidate_id"], "YOUTUBE",
                 next_position, 0, now, now),
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
            """SELECT job_id FROM download_queue WHERE status='WAITING'
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
        if status == RUNNING and not started:
            started = now
        if status in {COMPLETED, FAILED, CANCELLED, SKIPPED}:
            finished = now
        if progress is None:
            progress = 100 if status == COMPLETED else row["progress"]
        self.database.verbinding.execute(
            """UPDATE download_queue SET status=?,progress=?,last_stage=?,
            error_code=?,last_error=?,started_at=?,finished_at=?,updated_at=?
            WHERE job_id=?""",
            (status, max(0, min(100, int(progress))), stage,
             error_code, error_message, started, finished, now, job_id),
        )
        self.database.verbinding.commit()
        self._log(job_id, row["status"], status)
        return self.get(job_id)

    def cancel(self, job_id):
        return self.transition(job_id, CANCELLED, stage="Geannuleerd")

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
            "DELETE FROM download_queue WHERE status='COMPLETED'"
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
        )
