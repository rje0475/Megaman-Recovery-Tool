import json
import logging
import sys
from datetime import datetime

from core.spotify.client import SpotifyApiError, SpotifyClient
from core.spotify.models import (
    LOW_CONFIDENCE,
    MANUAL_REVIEW,
    MATCHED,
    NOT_FOUND,
    SpotifyMatch,
    RecoverySetInfo,
    SpotifySearchSummary,
)
from core.spotify.parsing import parseer_recovery_itemnaam
from core.spotify.scoring import bereken_score
from core.progress import maak_progress


MATCH_THRESHOLD = 0.95
LOW_CONFIDENCE_THRESHOLD = 0.90
MANUAL_REVIEW_THRESHOLD = 0.80
MAX_SAFE_BATCH = 500
AUTOMATIC_STATUSES = (
    MATCHED, LOW_CONFIDENCE, NOT_FOUND, MANUAL_REVIEW,
)
MANUAL_STATUSES = ("MANUAL", "REVIEWED_NONE")
LOGGER = logging.getLogger(__name__)


class SpotifyRecoverySetError(RuntimeError):
    pass


def _lokale_duur_ms(bestand):
    if not bestand:
        return None
    try:
        from mutagen import File
        audio = File(bestand)
        return (
            round(audio.info.length * 1000)
            if audio and getattr(audio, "info", None)
            else None
        )
    except (OSError, ValueError):
        return None
    except Exception:
        return None


def zoekopdrachten(artiest, titel):
    parsed = parseer_recovery_itemnaam(
        f"{artiest} - {titel}", artiest, titel
    )
    artiest, titel = parsed.artist, parsed.title
    return (
        ("FIELD_FILTERS", f'artist:"{artiest}" track:"{titel}"'),
        ("ARTIST_TITLE", f"{artiest} {titel}".strip()),
        ("TITLE_ONLY", titel),
    )


def zoek_beste_match(
    client, artiest, titel, duur_ms=None, kandidaten_callback=None
):
    parsed = parseer_recovery_itemnaam(
        f"{artiest} - {titel}", artiest, titel
    )
    artiest, titel = parsed.artist, parsed.title
    kandidaten = {}
    for methode, query in zoekopdrachten(artiest, titel):
        for track in client.search_tracks(query, limit=20):
            score = bereken_score(artiest, titel, duur_ms, track)
            LOGGER.debug(
                "Spotify-kandidaat %s - %s via %s: "
                "titel=%.1f artiest=%.1f extra_artiesten=%.1f "
                "duur=%.1f normalisatie=%.1f totaal=%.1f",
                ", ".join(track.artists), track.title, methode,
                score.title * 100, score.primary_artist * 100,
                score.extra_artists * 100, score.duration * 100,
                score.normalization * 100, score.total * 100,
            )
            if score.rejected:
                LOGGER.debug(
                    "Spotify-kandidaat %s afgewezen: %s",
                    track.track_id, score.rejection_reason,
                )
                continue
            bestaand = kandidaten.get(track.track_id)
            if bestaand is None or score.total > bestaand[0]:
                kandidaten[track.track_id] = (
                    score.total, methode, track
                )
    if not kandidaten:
        if kandidaten_callback:
            kandidaten_callback(())
        return SpotifyMatch(None, None, None, NOT_FOUND)
    gerangschikt = tuple(sorted(
        kandidaten.values(),
        key=lambda kandidaat: kandidaat[0],
        reverse=True,
    ))
    if kandidaten_callback:
        kandidaten_callback(gerangschikt)
    score, methode, track = gerangschikt[0]
    if score >= MATCH_THRESHOLD:
        status = MATCHED
    elif score >= LOW_CONFIDENCE_THRESHOLD:
        status = LOW_CONFIDENCE
    elif score >= MANUAL_REVIEW_THRESHOLD:
        status = MANUAL_REVIEW
    else:
        return SpotifyMatch(None, score, methode, NOT_FOUND)
    return SpotifyMatch(track, score, methode, status)


def _bewaar_kandidaten(
    database, item_id, kandidaten, artiest, titel, duur_ms
):
    database.verbinding.execute(
        "DELETE FROM spotify_candidates WHERE recovery_item_id=?",
        (item_id,),
    )
    for rang, (score, methode, track) in enumerate(kandidaten, 1):
        details = bereken_score(artiest, titel, duur_ms, track)
        database.verbinding.execute(
            """
            INSERT INTO spotify_candidates (
              recovery_item_id, spotify_track_id, spotify_uri, spotify_url,
              artist, title, album, album_cover_url, duration_ms, popularity,
              total_score, artist_score, title_score, version_score,
              duration_score, rank_number, search_strategy, search_query,
              selected, rejected, score_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?,
                      '', 0, 0, '')
            """,
            (
                item_id, track.track_id, track.uri, track.url,
                ", ".join(track.artists), track.title, track.album,
                track.album_cover_url, track.duration_ms, track.popularity,
                score, details.primary_artist, details.title,
                details.duration, rang, methode,
            ),
        )
    database.verbinding.commit()


def _bewaar_match(database, item_id, match):
    track = match.track
    database.verbinding.execute(
        """
        UPDATE recovery_items SET
          spotify_track_id=?, spotify_uri=?, spotify_url=?,
          spotify_album=?, spotify_artists=?, spotify_title=?,
          spotify_duration_ms=?, spotify_popularity=?,
          spotify_confidence=?, spotify_search_method=?,
          spotify_status=?, spotify_last_checked=?
        WHERE id=?
        """,
        (
            track.track_id if track else None,
            track.uri if track else None,
            track.url if track else None,
            track.album if track else None,
            json.dumps(track.artists, ensure_ascii=False) if track else None,
            track.title if track else None,
            track.duration_ms if track else None,
            track.popularity if track else None,
            match.confidence,
            match.search_method,
            match.status,
            datetime.now().isoformat(timespec="seconds"),
            item_id,
        ),
    )
    database.verbinding.commit()


def definitieve_spotify_telling(database, recovery_set_id):
    telling = {
        MATCHED: 0,
        LOW_CONFIDENCE: 0,
        MANUAL_REVIEW: 0,
        NOT_FOUND: 0,
        "ERROR": 0,
    }
    for rij in database.verbinding.execute(
        """
        SELECT spotify_status, COUNT(*) aantal
        FROM recovery_items
        WHERE recovery_set_id=?
          AND probleem_bron LIKE '%salvage%'
        GROUP BY spotify_status
        """,
        (recovery_set_id,),
    ):
        status = rij["spotify_status"]
        if status in MANUAL_STATUSES:
            status = MANUAL_REVIEW
        if status in telling:
            telling[status] += rij["aantal"]
    return telling


def beschikbare_recovery_sets(database):
    rijen = database.verbinding.execute(
        """
        SELECT s.id, s.archive_set_name, s.archive_name,
               s.created_at, s.updated_at, COUNT(r.id) recovery_item_count
        FROM recovery_sets s
        LEFT JOIN recovery_items r
          ON r.recovery_set_id=s.id
         AND r.probleem_bron LIKE '%salvage%'
        GROUP BY s.id
        ORDER BY s.updated_at DESC, s.id DESC
        """
    ).fetchall()
    return tuple(
        RecoverySetInfo(
            rij["id"], rij["archive_set_name"], rij["archive_name"],
            rij["recovery_item_count"], rij["created_at"], rij["updated_at"],
        )
        for rij in rijen
    )


def _selecteer_recovery_set(
    database, recovery_set_id=None, archive_set_name=None
):
    voorwaarden, waarden = [], []
    if recovery_set_id is not None:
        voorwaarden.append("s.id=?")
        waarden.append(int(recovery_set_id))
    if archive_set_name is not None:
        voorwaarden.append("s.archive_set_name=? COLLATE NOCASE")
        waarden.append(str(archive_set_name).strip())
    if voorwaarden:
        rij = database.verbinding.execute(
            f"""
            SELECT s.*, COUNT(r.id) recovery_item_count
            FROM recovery_sets s
            LEFT JOIN recovery_items r
              ON r.recovery_set_id=s.id
             AND r.probleem_bron LIKE '%salvage%'
            WHERE {' AND '.join(voorwaarden)}
            GROUP BY s.id
            """,
            waarden,
        ).fetchone()
        automatisch = False
    else:
        rij = database.verbinding.execute(
            """
            SELECT s.*, COUNT(r.id) recovery_item_count
            FROM recovery_sets s
            JOIN recovery_items r
              ON r.recovery_set_id=s.id
             AND r.probleem_bron LIKE '%salvage%'
            GROUP BY s.id
            HAVING COUNT(r.id)>0
            ORDER BY s.updated_at DESC, s.id DESC
            LIMIT 1
            """
        ).fetchone()
        automatisch = True
    if rij is None:
        raise SpotifyRecoverySetError(
            "Geen geldige recovery-set met definitieve recovery-items gevonden."
        )
    return rij, automatisch


def voer_spotify_search_uit(
    database, recovery_set_id=None, archive_set_name=None,
    force=False, allow_large_batch=False, client=None, uitvoer=None,
    progress_callback=None, verbose_items=True,
):
    uitvoer = uitvoer or sys.stdout
    recovery_set, automatisch = _selecteer_recovery_set(
        database, recovery_set_id, archive_set_name
    )
    set_id = recovery_set["id"]
    set_naam = recovery_set["archive_set_name"]
    totaal = recovery_set["recovery_item_count"]
    if automatisch:
        uitvoer.write(
            f"Automatisch geselecteerde recovery-set: {set_naam}\n"
        )
    rijen = database.verbinding.execute(
        """
        SELECT r.*, m.bestand AS local_file, s.status AS smart_status
        FROM recovery_items r
        LEFT JOIN mp3_bestanden m ON m.id=r.mp3_id
        LEFT JOIN spotify_smart_results s ON s.recovery_item_id=r.id
        WHERE r.recovery_set_id=?
          AND r.probleem_bron LIKE '%salvage%'
        ORDER BY r.id
        """,
        (set_id,),
    ).fetchall()
    handmatige_ids = {
        item["id"] for item in rijen
        if item["smart_status"] in MANUAL_STATUSES
        or item["spotify_status"] in MANUAL_STATUSES
    }
    automatische_ids = {
        item["id"] for item in rijen
        if item["id"] not in handmatige_ids
        and item["spotify_status"] in AUTOMATIC_STATUSES
    }
    items = tuple(
        item for item in rijen
        if item["id"] not in handmatige_ids
        and (force or item["id"] not in automatische_ids)
    )
    uitvoer.write(
        f"Spotify recovery-set: {set_naam}\n"
        f"Recovery-set-ID: {set_id}\n"
        f"Te verwerken recovery-items: {len(items)}\n"
        f"Reeds verwerkt en overgeslagen: "
        f"{0 if force else len(automatische_ids)}\n"
        f"Handmatige keuzes overgeslagen: {len(handmatige_ids)}\n"
    )
    if totaal > MAX_SAFE_BATCH and not allow_large_batch:
        raise SpotifyRecoverySetError(
            "Spotify-zoekopdracht afgebroken.\n"
            f"Geselecteerde recovery-set: {set_naam}\n"
            f"Aantal recovery-items: {totaal}\n"
            "Dit lijkt geen gerichte recovery-set te zijn."
        )
    client = client or (SpotifyClient.from_environment() if items else None)
    telling = {
        MATCHED: 0, LOW_CONFIDENCE: 0,
        NOT_FOUND: 0, MANUAL_REVIEW: 0,
    }
    def meld_progress(message):
        if not progress_callback:
            return
        definitief = definitieve_spotify_telling(database, set_id)
        verwerkt = sum(definitief.values())
        progress_callback(maak_progress(
            "Spotify Search",
            verwerkt,
            totaal,
            message,
            matched_count=definitief[MATCHED],
            low_confidence_count=definitief[LOW_CONFIDENCE],
            manual_review_count=definitief[MANUAL_REVIEW],
            not_found_count=definitief[NOT_FOUND],
            error_count=definitief["ERROR"],
        ))

    meld_progress("Spotify-resultaten voorbereiden.")
    for item in items:
        parsed = parseer_recovery_itemnaam(
            item["verwacht_rel_pad"],
            item["bepaalde_artiest"],
            item["bepaalde_titel"],
        )
        artiest, titel = parsed.artist, parsed.title
        _bewaar_kandidaten(
            database, item["id"], (), artiest, titel, None
        )
        if verbose_items:
            uitvoer.write(
                f"\nSpotify zoeken:\n{artiest} - {titel}\n"
            )
        if not artiest or not titel:
            match = SpotifyMatch(None, None, None, MANUAL_REVIEW)
        else:
            try:
                kandidaten = []
                duur_ms = _lokale_duur_ms(item["local_file"])
                match = zoek_beste_match(
                    client, artiest, titel,
                    duur_ms,
                    kandidaten_callback=kandidaten.extend,
                )
                _bewaar_kandidaten(
                    database, item["id"], kandidaten,
                    artiest, titel, duur_ms,
                )
            except SpotifyApiError as error:
                if verbose_items:
                    uitvoer.write(f"Spotify API-fout: {error}\n")
                match = SpotifyMatch(None, None, None, MANUAL_REVIEW)
        _bewaar_match(database, item["id"], match)
        telling[match.status] += 1
        meld_progress(f"{artiest} - {titel} verwerkt.")
        if match.track and verbose_items:
            uitvoer.write(
                f"Match:\n{', '.join(match.track.artists)} - "
                f"{match.track.title}\nConfidence:\n"
                f"{match.confidence:.0%}\nStatus:\n{match.status}\n"
            )
        elif verbose_items:
            uitvoer.write(
                "Geen Spotify-resultaat gevonden\n"
                f"Status:\n{match.status}\n"
            )
    overgeslagen_automatisch = 0 if force else len(automatische_ids)
    overgeslagen = overgeslagen_automatisch + len(handmatige_ids)
    uitvoer.write(
        f"\nRecovery-set: {set_naam}\n"
        f"Totaal in set: {totaal}\n"
        f"Verwerkt: {len(items)}\n"
        f"Overgeslagen: {overgeslagen}\n"
        f"MATCHED: {telling[MATCHED]}\n"
        f"LOW_CONFIDENCE: {telling[LOW_CONFIDENCE]}\n"
        f"NOT_FOUND: {telling[NOT_FOUND]}\n"
        f"MANUAL_REVIEW: {telling[MANUAL_REVIEW]}\n"
    )
    return SpotifySearchSummary(
        set_id, set_naam, totaal, len(items), overgeslagen,
        telling[MATCHED], telling[LOW_CONFIDENCE],
        telling[NOT_FOUND], telling[MANUAL_REVIEW],
        overgeslagen_automatisch, len(handmatige_ids),
    )
