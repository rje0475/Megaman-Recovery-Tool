"""Transactioneel en idempotent opruimen van geregistreerde RAR-volumes."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


LOGGER = logging.getLogger(__name__)
_RAR_VOLUME = re.compile(r"(?i)(?:\.part\d+\.rar|\.rar|\.r\d+)$")


@dataclass(frozen=True)
class RarCleanupResult:
    cleanup_id: int
    status: str
    removed: tuple[Path, ...]
    failed: dict[str, str]
    remaining: tuple[Path, ...]


def _now():
    return datetime.now().isoformat(timespec="seconds")


def registreer_rar_cleanup(
    database, salvage_run_id, rar_set_key, source_root, volumes,
    enabled, eligible, reason,
):
    """Leg de exacte cleanup-set vast nadat de salvage-run gecommit is."""
    status = "pending" if enabled and eligible else (
        "cleanup_disabled" if not enabled else "retained"
    )
    paths = [str(Path(volume).absolute()) for volume in volumes]
    now = _now()
    cursor = database.verbinding.execute(
        """
        INSERT INTO rar_cleanup_runs (
          salvage_run_id, rar_set_key, source_root, planned_volumes,
          removed_volumes, failed_volumes, status, reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '[]', '{}', ?, ?, ?, ?)
        ON CONFLICT(salvage_run_id) DO UPDATE SET
          reason=excluded.reason, updated_at=excluded.updated_at
        """,
        (salvage_run_id, rar_set_key, str(Path(source_root).resolve()),
         json.dumps(paths, ensure_ascii=False), status, reason, now, now),
    )
    database.verbinding.commit()
    if cursor.lastrowid:
        return cursor.lastrowid
    return database.verbinding.execute(
        "SELECT id FROM rar_cleanup_runs WHERE salvage_run_id=?",
        (salvage_run_id,),
    ).fetchone()["id"]


def _veilig_volume(path, source_root):
    raw = Path(path)
    if raw.is_symlink():
        return False, "symlink wordt niet verwijderd"
    try:
        resolved = raw.resolve(strict=False)
        root = Path(source_root).resolve(strict=True)
    except OSError as error:
        return False, f"padcontrole mislukt: {error}"
    if not resolved.is_relative_to(root):
        return False, "volume ligt buiten de bronmap"
    if not _RAR_VOLUME.search(raw.name):
        return False, "bestand is geen herkend RAR-volume"
    return True, None


def voer_rar_cleanup_uit(database, cleanup_id, logger=None, remover=None):
    """Verwijder uitsluitend geregistreerde volumes; stop bij de eerste fout."""
    logger = logger or LOGGER
    remover = remover or (lambda path: path.unlink())
    row = database.verbinding.execute(
        "SELECT * FROM rar_cleanup_runs WHERE id=?", (cleanup_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Onbekende RAR-cleanup: {cleanup_id}")
    planned = tuple(Path(value) for value in json.loads(row["planned_volumes"]))
    removed = list(json.loads(row["removed_volumes"] or "[]"))
    failures = dict(json.loads(row["failed_volumes"] or "{}"))
    logger.info(
        "RAR-cleanup set=%s status=%s volumes=%s",
        row["rar_set_key"], row["status"], [str(path) for path in planned],
    )
    if row["status"] in {"cleanup_disabled", "retained"}:
        return RarCleanupResult(
            cleanup_id, row["status"], tuple(map(Path, removed)), failures,
            tuple(path for path in planned if str(path) not in removed),
        )
    if row["status"] == "removed":
        return RarCleanupResult(
            cleanup_id, "removed", tuple(map(Path, removed)), failures, (),
        )

    failures = {}
    for path in planned:
        text = str(path)
        if text in removed:
            continue
        safe, error = _veilig_volume(path, row["source_root"])
        if not safe:
            failures[text] = error
        elif not path.exists():
            # Exact geregistreerd en inmiddels afwezig: crash kan tussen unlink
            # en journal-update hebben plaatsgevonden. Dit is idempotent klaar.
            removed.append(text)
            logger.info("RAR-volume reeds verwijderd: %s", path)
        else:
            try:
                remover(path)
                removed.append(text)
                logger.info("RAR-volume verwijderd: %s", path)
            except OSError as exc:
                failures[text] = str(exc)
                logger.error("RAR-volume verwijderen mislukt: %s: %s", path, exc)
        status = "partial_cleanup" if failures else "pending"
        database.verbinding.execute(
            """
            UPDATE rar_cleanup_runs
            SET removed_volumes=?, failed_volumes=?, status=?, updated_at=?
            WHERE id=?
            """,
            (json.dumps(removed, ensure_ascii=False),
             json.dumps(failures, ensure_ascii=False), status, _now(), cleanup_id),
        )
        database.verbinding.commit()
        if failures:
            break

    remaining = tuple(path for path in planned if str(path) not in removed)
    final = "partial_cleanup" if failures or remaining else "removed"
    database.verbinding.execute(
        "UPDATE rar_cleanup_runs SET status=?, updated_at=? WHERE id=?",
        (final, _now(), cleanup_id),
    )
    database.verbinding.commit()
    logger.info("RAR-cleanup eindstatus set=%s status=%s", row["rar_set_key"], final)
    return RarCleanupResult(
        cleanup_id, final, tuple(map(Path, removed)), failures, remaining,
    )


def hervat_rar_cleanups(database, logger=None, remover=None):
    ids = [row["id"] for row in database.verbinding.execute(
        "SELECT id FROM rar_cleanup_runs WHERE status IN ('pending','partial_cleanup')"
    ).fetchall()]
    return tuple(
        voer_rar_cleanup_uit(database, cleanup_id, logger, remover)
        for cleanup_id in ids
    )
