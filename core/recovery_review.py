"""Domeinlogica en persistentie voor de Recovery Review Wizard."""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath

from core.spotify.search import MATCH_THRESHOLD


_CHARTCODE = re.compile(r"^(?P<code>\d{8})\s+(?P<name>.+)$")
_VERSIES = {
    "Live": (r"\blive\b",),
    "Remix": (r"\bremix\b",),
    "Radio Edit": (r"\bradio edit\b", r"\bradio version\b"),
    "Extended Mix": (r"\bextended(?: mix)?\b",),
    "Acoustic": (r"\bacoustic\b",),
    "Instrumental": (r"\binstrumental\b",),
    "Karaoke": (r"\bkaraoke\b",),
    "Remastered": (r"\bremaster(?:ed)?\b",),
    "Cover": (r"\bcover\b",),
    "Tribute": (r"\btribute\b",),
    "Sped Up": (r"\bsped up\b",),
    "Slowed": (r"\bslowed\b", r"\bslowed down\b"),
}
_NEAR_EQUAL_MARGIN = 0.02


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
    release_date: str | None
    duration_ms: int | None
    popularity: int | None
    confidence: float
    selected: bool
    version_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryReviewItem:
    id: int
    year: int | None
    week: int | None
    chart_position: int | None
    original_filename: str
    artist: str
    title: str
    version: str | None
    recovery_status: str
    spotify_status: str
    selected_for_playlist: bool
    selected_candidate_id: int | None
    selected_spotify_uri: str | None
    match_review_status: str | None
    candidates: tuple[ReviewCandidate, ...]
    selected_youtube_candidate_id: int | None = None
    selected_youtube_url: str | None = None
    youtube_review_status: str | None = None
    youtube_last_searched: str | None = None
    youtube_search_error: str | None = None
    preferred_audio_source: str | None = None

    @property
    def has_match(self):
        return bool(self.selected_candidate_id)

    @property
    def ready_for_playlist(self):
        return bool(
            self.selected_for_playlist
            and self.selected_candidate_id
            and self.selected_spotify_uri
        )


def _normaliseer(waarde):
    waarde = unicodedata.normalize("NFKD", str(waarde or "")).casefold()
    return re.sub(
        r"\s+", " ",
        "".join(
            teken for teken in waarde
            if not unicodedata.combining(teken)
        ),
    ).strip()


def herken_versies(waarde):
    tekst = _normaliseer(waarde)
    return tuple(
        naam
        for naam, patronen in _VERSIES.items()
        if any(re.search(patroon, tekst) for patroon in patronen)
    )


def versie_waarschuwingen(origineel, kandidaat):
    origineel_versies = set(herken_versies(origineel))
    kandidaat_versies = set(herken_versies(kandidaat))
    waarschuwingen = []
    if "Radio Edit" in origineel_versies and "Radio Edit" not in kandidaat_versies:
        waarschuwingen.append("Versie wijkt mogelijk af")
    for versie in (
        "Live", "Acoustic", "Instrumental", "Karaoke", "Remastered",
        "Cover", "Tribute", "Sped Up", "Slowed", "Extended Mix",
    ):
        if versie not in origineel_versies and versie in kandidaat_versies:
            waarschuwingen.append(
                "Live-versie" if versie == "Live" else f"{versie}-versie"
            )
    if "Remix" in kandidaat_versies:
        if "Remix" not in origineel_versies:
            waarschuwingen.append("Remix-versie")
        else:
            origineel_norm = _normaliseer(origineel)
            kandidaat_norm = _normaliseer(kandidaat)
            if (
                kandidaat_norm not in origineel_norm
                and origineel_norm not in kandidaat_norm
            ):
                waarschuwingen.append("Andere remix mogelijk")
    return tuple(dict.fromkeys(waarschuwingen))


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


def selecteer_spotify_kandidaat(
    database, recovery_item_id, candidate_id, review_status="USER_SELECTED"
):
    kandidaat = database.verbinding.execute(
        """
        SELECT c.*
        FROM spotify_candidates c
        JOIN recovery_items r ON r.id=c.recovery_item_id
        WHERE c.id=? AND c.recovery_item_id=?
        """,
        (int(candidate_id), int(recovery_item_id)),
    ).fetchone()
    if kandidaat is None:
        raise ValueError("Spotify-kandidaat hoort niet bij dit recovery-item.")
    nu = datetime.now().isoformat(timespec="seconds")
    database.verbinding.execute(
        """
        UPDATE spotify_candidates
        SET selected=CASE WHEN id=? THEN 1 ELSE 0 END
        WHERE recovery_item_id=?
        """,
        (int(candidate_id), int(recovery_item_id)),
    )
    database.verbinding.execute(
        """
        UPDATE recovery_items SET
          selected_spotify_candidate_id=?,
          selected_spotify_uri=?,
          selected_spotify_track_id=?,
          selected_spotify_artist=?,
          selected_spotify_title=?,
          selected_spotify_album=?,
          selected_spotify_duration_ms=?,
          selected_spotify_confidence=?,
          match_review_status=?,
          match_reviewed_at=?
        WHERE id=?
        """,
        (
            kandidaat["id"], kandidaat["spotify_uri"],
            kandidaat["spotify_track_id"], kandidaat["artist"],
            kandidaat["title"], kandidaat["album"],
            kandidaat["duration_ms"], kandidaat["total_score"],
            review_status, nu, int(recovery_item_id),
        ),
    )
    database.verbinding.commit()


def stel_playlist_selectie_in(database, recovery_item_id, selected):
    database.verbinding.execute(
        """
        UPDATE recovery_items
        SET playlist_selected=?, reviewed_at=?
        WHERE id=?
        """,
        (
            int(bool(selected)),
            datetime.now().isoformat(timespec="seconds"),
            int(recovery_item_id),
        ),
    )
    database.verbinding.commit()


def _mag_automatisch_selecteren(rij, kandidaten, origineel):
    if rij["spotify_status"] != "MATCHED" or not kandidaten:
        return False
    beste = kandidaten[0]
    if beste.confidence < MATCH_THRESHOLD:
        return False
    if beste.version_warnings:
        return False
    if (
        len(kandidaten) > 1
        and beste.confidence - kandidaten[1].confidence
        <= _NEAR_EQUAL_MARGIN
    ):
        return False
    return not versie_waarschuwingen(origineel, beste.title)


def laad_recovery_review(
    database, recovery_set_id, apply_auto_selection=True
):
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
        _, _, _, bestandsnaam = _chartgegevens(rij["verwacht_rel_pad"])
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
                release_date=kandidaat["release_date"],
                duration_ms=kandidaat["duration_ms"],
                popularity=kandidaat["popularity"],
                confidence=float(kandidaat["total_score"] or 0),
                selected=bool(kandidaat["selected"]),
                version_warnings=versie_waarschuwingen(
                    bestandsnaam,
                    f"{kandidaat['title'] or ''} "
                    f"{kandidaat['album'] or ''}",
                ),
            )
            for kandidaat in database.verbinding.execute(
                """
                SELECT *
                FROM spotify_candidates
                WHERE recovery_item_id=?
                ORDER BY total_score DESC, rank_number, id
                """,
                (rij["id"],),
            )
        )
        selected_id = rij["selected_spotify_candidate_id"]
        if selected_id is None:
            selected_id = next(
                (kandidaat.id for kandidaat in kandidaten if kandidaat.selected),
                None,
            )
        if (
            selected_id is not None
            and (
                rij["selected_spotify_candidate_id"] is None
                or not rij["selected_spotify_uri"]
            )
        ):
            selecteer_spotify_kandidaat(
                database, rij["id"], selected_id,
                rij["match_review_status"] or "MIGRATED_SELECTION",
            )
            rij = database.verbinding.execute(
                "SELECT * FROM recovery_items WHERE id=?", (rij["id"],)
            ).fetchone()
        if (
            selected_id is None
            and apply_auto_selection
            and _mag_automatisch_selecteren(rij, kandidaten, bestandsnaam)
        ):
            selecteer_spotify_kandidaat(
                database, rij["id"], kandidaten[0].id, "AUTO_SELECTED"
            )
            selected_id = kandidaten[0].id
            rij = database.verbinding.execute(
                "SELECT * FROM recovery_items WHERE id=?", (rij["id"],)
            ).fetchone()
        kandidaten = tuple(
            ReviewCandidate(
                **{
                    **kandidaat.__dict__,
                    "selected": kandidaat.id == selected_id,
                }
            )
            for kandidaat in kandidaten
        )
        jaar, week, positie, bestandsnaam = _chartgegevens(
            rij["verwacht_rel_pad"]
        )
        artiesten = rij["spotify_artists"]
        try:
            spotify_artiesten = json.loads(artiesten) if artiesten else []
        except (TypeError, ValueError):
            spotify_artiesten = []
        versie = herken_versies(bestandsnaam)
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
            version=", ".join(versie) if versie else None,
            recovery_status=_recovery_status(rij),
            spotify_status=_spotify_status(
                rij["spotify_status"], len(kandidaten)
            ),
            selected_for_playlist=bool(rij["playlist_selected"]),
            selected_candidate_id=selected_id,
            selected_spotify_uri=rij["selected_spotify_uri"],
            match_review_status=rij["match_review_status"],
            candidates=kandidaten,
            selected_youtube_candidate_id=rij["selected_youtube_candidate_id"],
            selected_youtube_url=rij["selected_youtube_url"],
            youtube_review_status=rij["youtube_review_status"],
            youtube_last_searched=rij["youtube_last_searched"],
            youtube_search_error=rij["youtube_search_error"],
            preferred_audio_source=rij["preferred_audio_source"],
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
    for item_id in geldige_items:
        stel_playlist_selectie_in(
            database, item_id, item_id in selected_item_ids
        )
    for item_id, kandidaat_id in candidate_by_item.items():
        if int(item_id) not in geldige_items:
            raise ValueError("Spotify-kandidaat hoort bij een andere set.")
        selecteer_spotify_kandidaat(
            database, item_id, kandidaat_id, "USER_SELECTED"
        )
