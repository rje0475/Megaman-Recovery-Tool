import json
import sys
from datetime import datetime

from core.spotify.client import SpotifyApiError, SpotifyClient
from core.spotify.models import (
    LOW_CONFIDENCE,
    MANUAL_REVIEW,
    MATCHED,
    NOT_FOUND,
    SpotifyMatch,
    SpotifySearchSummary,
)
from core.spotify.scoring import score_track


MATCH_THRESHOLD = 0.85


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
    return (
        ("FIELD_FILTERS", f'artist:"{artiest}" track:"{titel}"'),
        ("ARTIST_TITLE", f"{artiest} {titel}".strip()),
        ("TITLE_ONLY", titel),
    )


def zoek_beste_match(client, artiest, titel, duur_ms=None):
    kandidaten = {}
    for methode, query in zoekopdrachten(artiest, titel):
        for track in client.search_tracks(query, limit=20):
            score = score_track(artiest, titel, duur_ms, track)
            bestaand = kandidaten.get(track.track_id)
            if bestaand is None or score > bestaand[0]:
                kandidaten[track.track_id] = (score, methode, track)
    if not kandidaten:
        return SpotifyMatch(None, None, None, NOT_FOUND)
    score, methode, track = max(
        kandidaten.values(), key=lambda kandidaat: kandidaat[0]
    )
    status = MATCHED if score >= MATCH_THRESHOLD else LOW_CONFIDENCE
    return SpotifyMatch(track, score, methode, status)


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


def voer_spotify_search_uit(database, client=None, uitvoer=None):
    uitvoer = uitvoer or sys.stdout
    client = client or SpotifyClient.from_environment()
    items = database.verbinding.execute(
        """
        SELECT r.*, m.bestand AS local_file
        FROM recovery_items r
        LEFT JOIN mp3_bestanden m ON m.id=r.mp3_id
        LEFT JOIN spotify_smart_results s ON s.recovery_item_id=r.id
        WHERE COALESCE(s.status, '') NOT IN ('MANUAL', 'REVIEWED_NONE')
        ORDER BY r.id
        """
    ).fetchall()
    handmatig = database.verbinding.execute(
        """
        SELECT COUNT(*) aantal
        FROM recovery_items r
        JOIN spotify_smart_results s ON s.recovery_item_id=r.id
        WHERE s.status IN ('MANUAL', 'REVIEWED_NONE')
        """
    ).fetchone()["aantal"]
    telling = {
        MATCHED: 0, LOW_CONFIDENCE: 0,
        NOT_FOUND: 0, MANUAL_REVIEW: 0,
    }
    for item in items:
        artiest = (item["bepaalde_artiest"] or "").strip()
        titel = (item["bepaalde_titel"] or "").strip()
        uitvoer.write(f"\nSpotify zoeken:\n{artiest} - {titel}\n")
        if not artiest or not titel:
            match = SpotifyMatch(None, None, None, MANUAL_REVIEW)
        else:
            try:
                match = zoek_beste_match(
                    client, artiest, titel,
                    _lokale_duur_ms(item["local_file"]),
                )
            except SpotifyApiError as error:
                uitvoer.write(f"Spotify API-fout: {error}\n")
                match = SpotifyMatch(None, None, None, MANUAL_REVIEW)
        _bewaar_match(database, item["id"], match)
        telling[match.status] += 1
        if match.track:
            uitvoer.write(
                f"Match:\n{', '.join(match.track.artists)} - "
                f"{match.track.title}\nConfidence:\n"
                f"{match.confidence:.0%}\nStatus:\n{match.status}\n"
            )
        else:
            uitvoer.write(
                "Geen Spotify-resultaat gevonden\n"
                f"Status:\n{match.status}\n"
            )
    return SpotifySearchSummary(
        len(items), telling[MATCHED], telling[LOW_CONFIDENCE],
        telling[NOT_FOUND], telling[MANUAL_REVIEW], handmatig,
    )
