import sys
from datetime import datetime

from core.spotify.client import SpotifyClient
from core.spotify.models import SpotifyPlaylistSummary


PLAYLIST_DESCRIPTION = "Recovered tracks from Megaman Recovery Tool"


class SpotifyPlaylistError(RuntimeError):
    pass


def _selecteer_recovery_set(
    database, recovery_set_id=None, archive_set_name=None
):
    if recovery_set_id is None and archive_set_name is None:
        raise SpotifyPlaylistError(
            "Geef recovery_set_id of archive_set_name op."
        )
    voorwaarden, waarden = [], []
    if recovery_set_id is not None:
        voorwaarden.append("id=?")
        waarden.append(int(recovery_set_id))
    if archive_set_name is not None:
        voorwaarden.append("archive_set_name=? COLLATE NOCASE")
        waarden.append(str(archive_set_name).strip())
    rij = database.verbinding.execute(
        f"SELECT * FROM recovery_sets WHERE {' AND '.join(voorwaarden)}",
        waarden,
    ).fetchone()
    if rij is None:
        raise SpotifyPlaylistError("Recovery-set niet gevonden.")
    return rij


def _bewaar_playlist(database, recovery_set_id, playlist_id, name):
    database.verbinding.execute(
        """
        UPDATE recovery_sets
        SET spotify_playlist_id=?, spotify_playlist_name=?, updated_at=?
        WHERE id=?
        """,
        (
            playlist_id, name,
            datetime.now().isoformat(timespec="seconds"),
            recovery_set_id,
        ),
    )
    database.verbinding.commit()


def maak_of_open_playlist(database, recovery_set, client):
    playlist_id = recovery_set["spotify_playlist_id"]
    if playlist_id:
        playlist = client.get_playlist(playlist_id)
        if playlist:
            return playlist, False

    naam = recovery_set["archive_set_name"]
    playlist = next(
        (
            kandidaat
            for kandidaat in client.list_current_user_playlists()
            if kandidaat.get("name") == naam
        ),
        None,
    )
    aangemaakt = playlist is None
    if aangemaakt:
        playlist = client.create_playlist(
            naam, PLAYLIST_DESCRIPTION, public=False
        )
    if not playlist or not playlist.get("id"):
        raise SpotifyPlaylistError(
            "Spotify gaf geen geldige playlist terug."
        )
    _bewaar_playlist(
        database, recovery_set["id"], playlist["id"],
        playlist.get("name") or naam,
    )
    return playlist, aangemaakt


def voeg_matched_tracks_toe(database, recovery_set_id, playlist, client):
    rijen = database.verbinding.execute(
        """
        SELECT spotify_track_id, spotify_uri
        FROM recovery_items
        WHERE recovery_set_id=?
          AND probleem_bron LIKE '%salvage%'
          AND spotify_status='MATCHED'
          AND spotify_track_id IS NOT NULL
          AND spotify_uri IS NOT NULL
        ORDER BY id
        """,
        (recovery_set_id,),
    ).fetchall()
    uniek = {}
    for rij in rijen:
        uniek.setdefault(rij["spotify_track_id"], rij["spotify_uri"])
    aanwezig = client.get_playlist_track_ids(playlist["id"])
    toe_te_voegen = tuple(
        uri for track_id, uri in uniek.items()
        if track_id not in aanwezig
    )
    if toe_te_voegen:
        client.add_playlist_items(playlist["id"], toe_te_voegen)
    reeds_aanwezig = sum(
        track_id in aanwezig for track_id in uniek
    )
    return len(uniek), len(toe_te_voegen), reeds_aanwezig


def _statusaantallen(database, recovery_set_id):
    return {
        rij["spotify_status"]: rij["aantal"]
        for rij in database.verbinding.execute(
            """
            SELECT spotify_status, COUNT(*) aantal
            FROM recovery_items
            WHERE recovery_set_id=?
              AND probleem_bron LIKE '%salvage%'
            GROUP BY spotify_status
            """,
            (recovery_set_id,),
        )
    }


def sync_playlist(
    database, recovery_set_id=None, archive_set_name=None,
    client=None, uitvoer=None,
):
    uitvoer = uitvoer or sys.stdout
    recovery_set = _selecteer_recovery_set(
        database, recovery_set_id, archive_set_name
    )
    client = client or SpotifyClient.from_environment()
    playlist, aangemaakt = maak_of_open_playlist(
        database, recovery_set, client
    )
    matched, toegevoegd, aanwezig = voeg_matched_tracks_toe(
        database, recovery_set["id"], playlist, client
    )
    aantallen = _statusaantallen(database, recovery_set["id"])
    summary = SpotifyPlaylistSummary(
        recovery_set_id=recovery_set["id"],
        archive_set_name=recovery_set["archive_set_name"],
        playlist_id=playlist["id"],
        playlist_name=playlist.get("name")
        or recovery_set["archive_set_name"],
        created=aangemaakt,
        matched_total=matched,
        added=toegevoegd,
        already_present=aanwezig,
        skipped_low_confidence=aantallen.get("LOW_CONFIDENCE", 0),
        skipped_not_found=aantallen.get("NOT_FOUND", 0),
        skipped_manual_review=aantallen.get("MANUAL_REVIEW", 0),
    )
    uitvoer.write(
        f"Recovery-set:\n{summary.archive_set_name}\n\n"
        f"Playlist:\n{summary.playlist_name}\n\n"
        f"Playlist-ID:\n{summary.playlist_id}\n\n"
        f"Nieuwe tracks toegevoegd:\n{summary.added}\n\n"
        f"Reeds aanwezig:\n{summary.already_present}\n\n"
        "Overgeslagen LOW_CONFIDENCE:\n"
        f"{summary.skipped_low_confidence}\n\n"
        f"Overgeslagen NOT_FOUND:\n{summary.skipped_not_found}\n"
    )
    return summary
