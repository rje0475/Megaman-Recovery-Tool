"""Expliciete, reviewgestuurde Spotify-playlistsynchronisatie."""

import re
import sys
import time
from datetime import datetime
from pathlib import PureWindowsPath

from core.spotify.auth import verkrijg_geldig_gebruikerstoken
from core.spotify.client import SpotifyApiError, SpotifyClient
from core.spotify.models import SpotifyPlaylistSummary


PLAYLIST_DESCRIPTION = "Recovered tracks from Megaman Recovery Tool"
GELDIGE_REVIEWSTATUSSEN = frozenset({
    "AUTO_SELECTED", "USER_SELECTED", "MIGRATED_SELECTION", "CONFIRMED",
})
_CHARTCODE = re.compile(r"^(\d{2})(\d{2})(\d{4})\s+")


class SpotifyPlaylistError(RuntimeError):
    pass


def _selecteer_recovery_set(database, recovery_set_id=None, archive_set_name=None):
    if recovery_set_id is None and archive_set_name is None:
        raise SpotifyPlaylistError("Geef recovery_set_id of archive_set_name op.")
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


def _playlist_url(playlist):
    return (
        (playlist.get("external_urls") or {}).get("spotify")
        or playlist.get("url")
        or (
            f"https://open.spotify.com/playlist/{playlist['id']}"
            if playlist.get("id") else None
        )
    )


def _bewaar_sync(
    database, recovery_set_id, *, playlist_id=None, playlist_url=None,
    playlist_name=None, track_count=0, status, error=None,
):
    database.verbinding.execute(
        """
        UPDATE recovery_sets SET
          spotify_playlist_id=COALESCE(?, spotify_playlist_id),
          spotify_playlist_url=COALESCE(?, spotify_playlist_url),
          spotify_playlist_name=COALESCE(?, spotify_playlist_name),
          spotify_playlist_track_count=?, spotify_playlist_synced_at=?,
          spotify_playlist_sync_status=?, spotify_playlist_last_error=?,
          updated_at=?
        WHERE id=?
        """,
        (
            playlist_id, playlist_url, playlist_name, int(track_count),
            datetime.now().isoformat(timespec="seconds"), status, error,
            datetime.now().isoformat(timespec="seconds"), recovery_set_id,
        ),
    )
    database.verbinding.commit()


def _call_with_rate_limit_retry(call, sleep=time.sleep):
    try:
        return call()
    except SpotifyApiError as error:
        if error.status_code != 429:
            raise
        sleep(max(0.0, float(error.retry_after or 1)))
        return call()


def maak_of_open_playlist(
    database, recovery_set, client, playlist_name=None, sleep=time.sleep
):
    playlist_id = recovery_set["spotify_playlist_id"]
    if playlist_id:
        playlist = _call_with_rate_limit_retry(
            lambda: client.get_playlist(playlist_id), sleep
        )
        if playlist:
            _bewaar_sync(
                database, recovery_set["id"], playlist_id=playlist["id"],
                playlist_url=_playlist_url(playlist),
                playlist_name=playlist.get("name") or recovery_set["archive_set_name"],
                track_count=recovery_set["spotify_playlist_track_count"] or 0,
                status="PREPARED",
            )
            return playlist, False

    naam = str(playlist_name or recovery_set["archive_set_name"]).strip()
    if not naam:
        naam = recovery_set["archive_set_name"]
    playlists = _call_with_rate_limit_retry(
        client.list_current_user_playlists, sleep
    )
    playlist = next(
        (kandidaat for kandidaat in playlists if kandidaat.get("name") == naam),
        None,
    )
    aangemaakt = playlist is None
    if aangemaakt:
        playlist = _call_with_rate_limit_retry(
            lambda: client.create_playlist(
                naam, PLAYLIST_DESCRIPTION, public=False
            ),
            sleep,
        )
    if not playlist or not playlist.get("id"):
        raise SpotifyPlaylistError("Spotify gaf geen geldige playlist terug.")
    _bewaar_sync(
        database, recovery_set["id"], playlist_id=playlist["id"],
        playlist_url=_playlist_url(playlist),
        playlist_name=playlist.get("name") or naam,
        track_count=recovery_set["spotify_playlist_track_count"] or 0,
        status="PREPARED",
    )
    return playlist, aangemaakt


def _sorteersleutel(rij):
    bestandsnaam = PureWindowsPath(
        str(rij["verwacht_rel_pad"] or "").replace("/", "\\")
    ).name
    match = _CHARTCODE.match(bestandsnaam)
    if match:
        jaar = int(match.group(1))
        jaar += 2000 if jaar < 70 else 1900
        return jaar, int(match.group(2)), int(match.group(3)), bestandsnaam.casefold()
    return 9999, 99, 9999, bestandsnaam.casefold()


def _geselecteerde_tracks(database, recovery_set_id):
    alle = database.verbinding.execute(
        """
        SELECT id, verwacht_rel_pad, playlist_selected,
               selected_spotify_candidate_id, selected_spotify_uri,
               selected_spotify_track_id, match_review_status,
               EXISTS (
                 SELECT 1 FROM spotify_candidates c
                 WHERE c.id=recovery_items.selected_spotify_candidate_id
                   AND c.recovery_item_id=recovery_items.id
               ) AS selected_candidate_exists
        FROM recovery_items
        WHERE recovery_set_id=? AND probleem_bron LIKE '%salvage%'
        """,
        (recovery_set_id,),
    ).fetchall()
    aangevinkt = [rij for rij in alle if rij["playlist_selected"]]
    bruikbaar = [
        rij for rij in aangevinkt
        if rij["selected_spotify_candidate_id"] is not None
        and bool(rij["selected_candidate_exists"])
        and bool(rij["selected_spotify_uri"])
        and rij["match_review_status"] in GELDIGE_REVIEWSTATUSSEN
    ]
    bruikbaar.sort(key=_sorteersleutel)
    uniek, duplicaten = [], 0
    gezien = set()
    for rij in bruikbaar:
        sleutel = rij["selected_spotify_track_id"] or rij["selected_spotify_uri"]
        if sleutel in gezien:
            duplicaten += 1
            continue
        gezien.add(sleutel)
        uniek.append(rij)
    return tuple(uniek), duplicaten, len(aangevinkt) - len(bruikbaar)


def _statusaantallen(database, recovery_set_id):
    return {
        rij["spotify_status"]: rij["aantal"]
        for rij in database.verbinding.execute(
            """
            SELECT spotify_status, COUNT(*) aantal
            FROM recovery_items
            WHERE recovery_set_id=? AND probleem_bron LIKE '%salvage%'
            GROUP BY spotify_status
            """,
            (recovery_set_id,),
        )
    }


def _voeg_tracks_toe(client, playlist_id, tracks, aanwezig, sleep=time.sleep):
    nieuw = []
    reeds = 0
    for rij in tracks:
        track_id = rij["selected_spotify_track_id"]
        if not track_id and rij["selected_spotify_uri"].startswith("spotify:track:"):
            track_id = rij["selected_spotify_uri"].rsplit(":", 1)[-1]
        if track_id and track_id in aanwezig:
            reeds += 1
        else:
            nieuw.append(rij["selected_spotify_uri"])
    toegevoegd = 0
    fout = None
    for begin in range(0, len(nieuw), 100):
        batch = tuple(nieuw[begin:begin + 100])
        try:
            _call_with_rate_limit_retry(
                lambda batch=batch: client.add_playlist_items(playlist_id, batch),
                sleep,
            )
        except Exception as error:
            if toegevoegd == 0:
                raise
            fout = str(error)
            break
        toegevoegd += len(batch)
    return toegevoegd, reeds, fout


def voeg_matched_tracks_toe(database, recovery_set_id, playlist, client):
    """Compatibele publieke helper, nu uitsluitend reviewgestuurd."""
    tracks, _duplicaten, _zonder_match = _geselecteerde_tracks(
        database, recovery_set_id
    )
    aanwezig = client.get_playlist_track_ids(playlist["id"])
    toegevoegd, reeds, fout = _voeg_tracks_toe(
        client, playlist["id"], tracks, aanwezig
    )
    if fout:
        raise SpotifyPlaylistError(fout)
    return len(tracks), toegevoegd, reeds


def _sync_met_client(
    database, recovery_set, client, playlist_name, sleep=time.sleep
):
    tracks, duplicaten, zonder_match = _geselecteerde_tracks(
        database, recovery_set["id"]
    )
    playlist, aangemaakt = maak_of_open_playlist(
        database, recovery_set, client, playlist_name, sleep
    )
    aanwezig = _call_with_rate_limit_retry(
        lambda: client.get_playlist_track_ids(playlist["id"]), sleep
    )
    toegevoegd, reeds, batchfout = _voeg_tracks_toe(
        client, playlist["id"], tracks, aanwezig, sleep
    )
    status = "PARTIAL" if batchfout else "SUCCESS"
    aantallen = _statusaantallen(database, recovery_set["id"])
    url = _playlist_url(playlist)
    naam = playlist.get("name") or playlist_name or recovery_set["archive_set_name"]
    _bewaar_sync(
        database, recovery_set["id"], playlist_id=playlist["id"],
        playlist_url=url, playlist_name=naam,
        track_count=len(aanwezig) + toegevoegd,
        status=status, error=batchfout,
    )
    return SpotifyPlaylistSummary(
        recovery_set_id=recovery_set["id"],
        archive_set_name=recovery_set["archive_set_name"],
        playlist_id=playlist["id"], playlist_name=naam,
        created=aangemaakt, matched_total=len(tracks), added=toegevoegd,
        already_present=reeds,
        skipped_low_confidence=aantallen.get("LOW_CONFIDENCE", 0),
        skipped_not_found=aantallen.get("NOT_FOUND", 0),
        skipped_manual_review=aantallen.get("MANUAL_REVIEW", 0),
        playlist_url=url, unique_selected=len(tracks),
        duplicates_skipped=duplicaten, unmatched_selected=zonder_match,
        sync_status=status, last_error=batchfout,
    )


def sync_playlist(
    database, recovery_set_id=None, archive_set_name=None,
    client=None, uitvoer=None, playlist_name=None,
    token_provider=None, client_factory=None, sleep=time.sleep,
):
    """Synchroniseer uitsluitend expliciet gereviewde URI's."""
    uitvoer = uitvoer or sys.stdout
    recovery_set = _selecteer_recovery_set(
        database, recovery_set_id, archive_set_name
    )
    expliciete_client = client is not None
    token_provider = token_provider or verkrijg_geldig_gebruikerstoken
    client_factory = client_factory or SpotifyClient.from_environment
    if client is None:
        client = client_factory(access_token=token_provider())
    try:
        summary = _sync_met_client(
            database, recovery_set, client, playlist_name, sleep
        )
    except SpotifyApiError as error:
        if error.status_code == 401 and not expliciete_client:
            try:
                client = client_factory(
                    access_token=token_provider(force_refresh=True)
                )
                summary = _sync_met_client(
                    database, recovery_set, client, playlist_name, sleep
                )
            except Exception as refresh_error:
                _bewaar_sync(
                    database, recovery_set["id"], status="FAILED",
                    error=f"Tokenvernieuwing mislukt: {refresh_error}",
                )
                raise SpotifyPlaylistError(
                    "Spotify-autorisatie is verlopen en vernieuwen is mislukt."
                ) from refresh_error
        else:
            omschrijving = {
                403: "Spotify weigert playlisttoegang (403).",
                404: "De Spotify-playlist is niet gevonden (404).",
                429: "Spotify-limiet bereikt (429). Probeer later opnieuw.",
            }.get(error.status_code, str(error))
            _bewaar_sync(
                database, recovery_set["id"], status="FAILED",
                error=omschrijving,
            )
            raise SpotifyPlaylistError(omschrijving) from error
    except Exception as error:
        _bewaar_sync(
            database, recovery_set["id"], status="FAILED", error=str(error)
        )
        raise SpotifyPlaylistError(f"Playlist-sync mislukt: {error}") from error
    uitvoer.write(
        f"Playlist: {summary.playlist_name}\n"
        f"Unieke geselecteerde tracks: {summary.unique_selected}\n"
        f"Reeds aanwezig: {summary.already_present}\n"
        f"Nieuw toegevoegd: {summary.added}\n"
        f"Duplicaten overgeslagen: {summary.duplicates_skipped}\n"
        f"Items zonder match: {summary.unmatched_selected}\n"
        f"Status: {summary.sync_status}\n"
    )
    return summary
