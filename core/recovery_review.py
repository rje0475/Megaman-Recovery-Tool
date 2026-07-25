"""Read-only review models plus explicit persistence of user selections."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath


_CHARTCODE = re.compile(r"^(?P<code>\d{8})\s+(?P<name>.+)$")


@dataclass(frozen=True)
class ReviewCandidate:
    id: int
    spotify_track_id: str
    spotify_uri: str | None
    spotify_url: str | None
    album_cover_url: str | None
    artist: str
    title: str
    album: str | None
    duration_ms: int | None
    popularity: int | None
    confidence: float
    selected: bool


@dataclass(frozen=True)
class RecoveryReviewItem:
    id: int
    year: int | None
    week: int | None
    chart_position: int | None
    original_filename: str
    artist: str
    title: str
    recovery_status: str
    spotify_status: str
    selected_for_playlist: bool
    candidates: tuple[ReviewCandidate, ...]


def _chartgegevens(relatief_pad):
    bestandsnaam = PureWindowsPath(
        str(relatief_pad or "").replace("/", "\\")
    ).name
    match = _CHARTCODE.match(bestandsnaam)
    if not match:
        return None, None, None, bestandsnaam
    code = match.group("code")
    jaar = int(code[:2])
    jaar += 2000 if jaar < 70 else 1900
    return jaar, int(code[2:4]), int(code[4:]), bestandsnaam


def _recovery_status(rij):
    if rij["feit_nul_bytes"]:
        return "Zero-byte"
    if rij["feit_ontbreekt"]:
        return "Missing"
    if rij["ffmpeg_fout"] or "ffmpeg" in (rij["probleem_bron"] or ""):
        return "FFmpeg failed"
    return "Corrupt"


def _spotify_status(status, kandidaat_aantal):
    status = str(status or "").upper()
    if status in {"MATCHED", "MANUAL"}:
        return "Match"
    if status in {"AMBIGUOUS", "MULTIPLE_MATCHES"}:
        return "Multiple Matches"
    if status == "LOW_CONFIDENCE":
        return "Low Confidence"
    if status in {"MANUAL_REVIEW", "REVIEWED_NONE"}:
        return (
            "Multiple Matches" if kandidaat_aantal > 1
            else "Manual Review"
        )
    return "Not Found"


def laad_recovery_review(database, recovery_set_id):
    rijen = database.verbinding.execute(
        """
        SELECT *
        FROM recovery_items
        WHERE recovery_set_id=?
          AND probleem_bron LIKE '%salvage%'
        ORDER BY verwacht_rel_pad_norm, id
        """,
        (int(recovery_set_id),),
    ).fetchall()
    resultaat = []
    for rij in rijen:
        kandidaten = tuple(
            ReviewCandidate(
                id=kandidaat["id"],
                spotify_track_id=kandidaat["spotify_track_id"],
                spotify_uri=kandidaat["spotify_uri"],
                spotify_url=kandidaat["spotify_url"],
                album_cover_url=kandidaat["album_cover_url"],
                artist=kandidaat["artist"] or "",
                title=kandidaat["title"] or "",
                album=kandidaat["album"],
                duration_ms=kandidaat["duration_ms"],
                popularity=kandidaat["popularity"],
                confidence=float(kandidaat["total_score"] or 0),
                selected=bool(kandidaat["selected"]),
            )
            for kandidaat in database.verbinding.execute(
                """
                SELECT *
                FROM spotify_candidates
                WHERE recovery_item_id=?
                ORDER BY rank_number, total_score DESC, id
                """,
                (rij["id"],),
            )
        )
        jaar, week, positie, bestandsnaam = _chartgegevens(
            rij["verwacht_rel_pad"]
        )
        artiesten = rij["spotify_artists"]
        try:
            spotify_artiesten = json.loads(artiesten) if artiesten else []
        except (TypeError, ValueError):
            spotify_artiesten = []
        resultaat.append(RecoveryReviewItem(
            id=rij["id"],
            year=jaar,
            week=week,
            chart_position=positie,
            original_filename=bestandsnaam,
            artist=rij["bepaalde_artiest"] or (
                spotify_artiesten[0] if spotify_artiesten else ""
            ),
            title=rij["bepaalde_titel"] or rij["spotify_title"] or "",
            recovery_status=_recovery_status(rij),
            spotify_status=_spotify_status(
                rij["spotify_status"], len(kandidaten)
            ),
            selected_for_playlist=bool(rij["playlist_selected"]),
            candidates=kandidaten,
        ))
    return tuple(resultaat)


def bewaar_recovery_review(
    database, recovery_set_id, selected_item_ids, candidate_by_item
):
    selected_item_ids = {int(item_id) for item_id in selected_item_ids}
    geldige_items = {
        rij["id"]
        for rij in database.verbinding.execute(
            """
            SELECT id FROM recovery_items
            WHERE recovery_set_id=?
              AND probleem_bron LIKE '%salvage%'
            """,
            (int(recovery_set_id),),
        )
    }
    if not selected_item_ids <= geldige_items:
        raise ValueError("Review bevat items uit een andere recovery-set.")
    nu = datetime.now().isoformat(timespec="seconds")
    database.verbinding.execute(
        """
        UPDATE recovery_items
        SET playlist_selected=0, reviewed_at=?
        WHERE recovery_set_id=? AND probleem_bron LIKE '%salvage%'
        """,
        (nu, int(recovery_set_id)),
    )
    for item_id in selected_item_ids:
        database.verbinding.execute(
            "UPDATE recovery_items SET playlist_selected=1 WHERE id=?",
            (item_id,),
        )
    for item_id, kandidaat_id in candidate_by_item.items():
        item_id, kandidaat_id = int(item_id), int(kandidaat_id)
        if item_id not in geldige_items:
            raise ValueError("Spotify-kandidaat hoort bij een andere set.")
        kandidaat = database.verbinding.execute(
            """
            SELECT id FROM spotify_candidates
            WHERE id=? AND recovery_item_id=?
            """,
            (kandidaat_id, item_id),
        ).fetchone()
        if kandidaat is None:
            raise ValueError("Spotify-kandidaat hoort niet bij dit item.")
        database.verbinding.execute(
            """
            UPDATE spotify_candidates
            SET selected=CASE WHEN id=? THEN 1 ELSE 0 END
            WHERE recovery_item_id=?
            """,
            (kandidaat_id, item_id),
        )
    database.verbinding.commit()
