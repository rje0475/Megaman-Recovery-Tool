import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from spotify import (
    SPOTIFY_PROVIDER,
    SpotifyApiFout,
    SpotifyClient,
    SpotifyConfiguratieFout,
    schoon_spotify_zoekwaarden,
)


MINIMALE_IDENTITEIT = 0.60
FOUND_DREMPEL = 0.82
AMBIGUOUS_DREMPEL = 0.55
NOT_FOUND_RETRY = timedelta(days=7)
ERROR_RETRY = timedelta(hours=1)
MAX_POGINGEN = 3


@dataclass(frozen=True)
class RecoverySpotifyResultaat:
    geschikt: int
    gevonden: int
    ambiguous: int
    niet_gevonden: int
    fouten: int
    onvoldoende_identiteit: int
    overgeslagen: int
    playlist_tracks: int
    credentials_ontbreken: bool = False


def _identiteit_handtekening(item):
    waarden = [
        item["bepaalde_artiest"],
        item["bepaalde_titel"],
        item["bepaald_album"],
        item["bepaald_tracknummer"],
        item["identiteit_betrouwbaarheid"],
        item["identiteit_bepaald_op"],
    ]
    return hashlib.sha256(
        json.dumps(waarden, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _normaliseer(waarde):
    tekst = unicodedata.normalize("NFKD", waarde or "")
    tekst = "".join(teken for teken in tekst if not unicodedata.combining(teken))
    tekst = re.sub(r"(?i)\b(?:feat|ft)\.?\b.*$", "", tekst)
    tekst = re.sub(
        r"(?i)\b(?:(?:19|20)\d{2}\s+)?remaster(?:ed)?"
        r"(?:\s+(?:19|20)\d{2})?\b",
        " ",
        tekst,
    )
    tekst = re.sub(
        r"(?i)\b(?:official\s+(?:video|audio)|lyrics?|hd|hq)\b",
        " ",
        tekst,
    )
    return re.sub(r"[^a-z0-9]+", " ", tekst.casefold()).strip()


def _overeenkomst(verwacht, gevonden):
    links = _normaliseer(verwacht)
    rechts = _normaliseer(gevonden)
    if not links or not rechts:
        return 0.0
    reeks = SequenceMatcher(None, links, rechts).ratio()
    links_tokens = set(links.split())
    rechts_tokens = set(rechts.split())
    token_score = len(links_tokens & rechts_tokens) / len(
        links_tokens | rechts_tokens
    )
    return (reeks + token_score) / 2


def bereken_matchscore(
    verwacht_artiest,
    verwacht_titel,
    gevonden_artiest,
    gevonden_titel,
    verwacht_album=None,
    gevonden_album=None,
    verwacht_duur_ms=None,
    gevonden_duur_ms=None,
):
    """Bereken een transparante gewogen score tussen 0 en 1."""

    artiest = _overeenkomst(verwacht_artiest, gevonden_artiest)
    titel = _overeenkomst(verwacht_titel, gevonden_titel)
    score = 0.5 * artiest + 0.5 * titel

    if verwacht_album and gevonden_album:
        score = 0.45 * artiest + 0.45 * titel
        score += 0.10 * _overeenkomst(verwacht_album, gevonden_album)

    if verwacht_duur_ms and gevonden_duur_ms:
        afwijking = abs(verwacht_duur_ms - gevonden_duur_ms)
        duur = max(0.0, 1.0 - afwijking / 15000)
        score = 0.95 * score + 0.05 * duur

    return round(max(0.0, min(score, 1.0)), 4)


def _verwachte_duur_ms(database, item):
    if item["mp3_id"] is None:
        return None
    rij = database.verbinding.execute(
        "SELECT bestand FROM mp3_bestanden WHERE id = ?",
        (item["mp3_id"],),
    ).fetchone()
    if rij is None:
        return None
    try:
        from mutagen import File
        audio = File(rij["bestand"])
        return round(audio.info.length * 1000) if audio and audio.info else None
    except (ImportError, OSError, AttributeError):
        return None


def _zoek_met_retries(client, artiest, titel, slaapfunctie):
    laatste_fout = None
    for poging in range(MAX_POGINGEN):
        try:
            if hasattr(client, "zoek_nummers"):
                return client.zoek_nummers(artiest, titel, limiet=10)
            resultaat = client.zoek_nummer(artiest, titel)
            return [resultaat] if resultaat.gevonden else []
        except SpotifyApiFout as fout:
            laatste_fout = fout
            if poging + 1 < MAX_POGINGEN:
                slaapfunctie(0.5 * (2 ** poging))
    raise laatste_fout


def _beste_match(item, kandidaten, verwacht_duur_ms):
    gescoord = []
    for kandidaat in kandidaten:
        score = bereken_matchscore(
            item["bepaalde_artiest"],
            item["bepaalde_titel"],
            kandidaat.artiest,
            kandidaat.titel,
            item["bepaald_album"],
            kandidaat.album,
            verwacht_duur_ms,
            kandidaat.duur_ms,
        )
        gescoord.append((score, kandidaat))
    return max(gescoord, key=lambda paar: paar[0], default=(0.0, None))


def _zoek_item(client, item, verwacht_duur_ms, slaapfunctie):
    artiest = item["bepaalde_artiest"]
    titel = item["bepaalde_titel"]
    kandidaten = _zoek_met_retries(
        client, artiest, titel, slaapfunctie
    )
    score, beste = _beste_match(item, kandidaten, verwacht_duur_ms)
    methode = "original"

    if beste is None or score < FOUND_DREMPEL:
        schone_artiest, schone_titel = schoon_spotify_zoekwaarden(
            artiest, titel
        )
        if (
            schone_artiest
            and schone_titel
            and (schone_artiest, schone_titel) != (artiest, titel)
        ):
            fallback = _zoek_met_retries(
                client, schone_artiest, schone_titel, slaapfunctie
            )
            fallback_score, fallback_beste = _beste_match(
                item, fallback, verwacht_duur_ms
            )
            if fallback_score > score:
                score, beste = fallback_score, fallback_beste
                methode = "cleaned"

    if beste is None:
        return "not_found", methode, score, None
    if score >= FOUND_DREMPEL:
        return "found", methode, score, beste
    if score >= AMBIGUOUS_DREMPEL:
        return "ambiguous", methode, score, beste
    return "not_found", methode, score, None


def _mag_zoeken(bestaand, identiteit_handtekening, nu):
    if bestaand is None:
        return True
    if bestaand["resultaat_type"] == "found":
        return False
    if bestaand["identiteit_handtekening"] != identiteit_handtekening:
        return True
    retry_na = bestaand["retry_na"]
    return retry_na is None or datetime.fromisoformat(retry_na) <= nu


def _bewaar_resultaat(
    database, item, resultaat_type, methode, score, kandidaat,
    foutmelding, identiteit_handtekening, nu
):
    retry_na = None
    if resultaat_type == "not_found":
        retry_na = nu + NOT_FOUND_RETRY
    elif resultaat_type == "error":
        retry_na = nu + ERROR_RETRY

    database.verbinding.execute(
        """
        INSERT INTO recovery_provider_resultaten (
            recovery_item_id, relatief_pad, provider,
            provider_track_id, provider_url, gevonden_artiest,
            gevonden_titel, gevonden_album, gevonden_duur_ms,
            zoekmethode, matchscore, resultaat_type, foutmelding,
            gezocht_op, retry_na, identiteit_handtekening
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (recovery_item_id, provider) DO UPDATE SET
            relatief_pad = excluded.relatief_pad,
            provider_track_id = excluded.provider_track_id,
            provider_url = excluded.provider_url,
            gevonden_artiest = excluded.gevonden_artiest,
            gevonden_titel = excluded.gevonden_titel,
            gevonden_album = excluded.gevonden_album,
            gevonden_duur_ms = excluded.gevonden_duur_ms,
            zoekmethode = excluded.zoekmethode,
            matchscore = excluded.matchscore,
            resultaat_type = excluded.resultaat_type,
            foutmelding = excluded.foutmelding,
            gezocht_op = excluded.gezocht_op,
            retry_na = excluded.retry_na,
            identiteit_handtekening = excluded.identiteit_handtekening
        """,
        (
            item["id"], item["verwacht_rel_pad"], SPOTIFY_PROVIDER,
            kandidaat.track_id if kandidaat else None,
            kandidaat.url if kandidaat else None,
            kandidaat.artiest if kandidaat else None,
            kandidaat.titel if kandidaat else None,
            kandidaat.album if kandidaat else None,
            kandidaat.duur_ms if kandidaat else None,
            methode, score, resultaat_type, foutmelding,
            nu.isoformat(timespec="seconds"),
            retry_na.isoformat(timespec="seconds") if retry_na else None,
            identiteit_handtekening or "",
        ),
    )
    database.verbinding.commit()


def exporteer_spotify_recovery_playlist(
    database, pad="spotify_recovery_playlist.json"
):
    """Schrijf uitsluitend definitieve matches via een atomische replace."""

    doel = Path(pad)
    doel.parent.mkdir(parents=True, exist_ok=True)
    rijen = database.verbinding.execute(
        """
        SELECT r.id AS recovery_item_id, p.provider_track_id,
               p.provider_url, p.gevonden_artiest, p.gevonden_titel,
               p.gevonden_album, r.verwacht_rel_pad, r.rar_set_key,
               p.matchscore
        FROM recovery_provider_resultaten AS p
        JOIN recovery_items AS r ON r.id = p.recovery_item_id
        WHERE p.provider = ? AND p.resultaat_type = 'found'
        ORDER BY r.id
        """,
        (SPOTIFY_PROVIDER,),
    ).fetchall()
    gegevens = [
        {
            "recovery_item_id": rij["recovery_item_id"],
            "spotify_track_id": rij["provider_track_id"],
            "spotify_url": rij["provider_url"],
            "artiest": rij["gevonden_artiest"],
            "titel": rij["gevonden_titel"],
            "album": rij["gevonden_album"],
            "verwacht_rel_pad": rij["verwacht_rel_pad"],
            "rar_set_key": rij["rar_set_key"],
            "matchscore": rij["matchscore"],
        }
        for rij in rijen
    ]
    tijdelijk = doel.with_name(f".{doel.name}.{os.getpid()}.tmp")
    try:
        tijdelijk.write_text(
            json.dumps(gegevens, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tijdelijk, doel)
    finally:
        if tijdelijk.exists():
            tijdelijk.unlink()
    return len(gegevens)


def voer_spotify_recovery_uit(
    database, client=None, uitvoer=None,
    export_pad="spotify_recovery_playlist.json", slaapfunctie=time.sleep,
    nu=None
):
    uitvoer = uitvoer or sys.stdout
    nu = nu or datetime.now()
    items = [dict(rij) for rij in database.verbinding.execute(
        "SELECT * FROM recovery_items ORDER BY id"
    )]
    geschikt = [
        item for item in items
        if item["bepaalde_artiest"] and item["bepaalde_titel"]
        and (item["identiteit_betrouwbaarheid"] or 0) >= MINIMALE_IDENTITEIT
    ]
    onvoldoende = len(items) - len(geschikt)
    gevonden = ambiguous = niet_gevonden = fouten = overgeslagen = 0
    credentials_ontbreken = False

    if client is None:
        try:
            client = SpotifyClient.uit_omgeving()
        except SpotifyConfiguratieFout:
            credentials_ontbreken = True
            uitvoer.write(
                "Spotify recovery overgeslagen: stel SPOTIFY_CLIENT_ID "
                "en SPOTIFY_CLIENT_SECRET in.\n"
            )

    if client is not None:
        for item in geschikt:
            bestaand = database.verbinding.execute(
                """
                SELECT * FROM recovery_provider_resultaten
                WHERE recovery_item_id = ? AND provider = ?
                """,
                (item["id"], SPOTIFY_PROVIDER),
            ).fetchone()
            handtekening = _identiteit_handtekening(item)
            if not _mag_zoeken(bestaand, handtekening, nu):
                overgeslagen += 1
                continue
            try:
                soort, methode, score, kandidaat = _zoek_item(
                    client, item, _verwachte_duur_ms(database, item),
                    slaapfunctie
                )
                _bewaar_resultaat(
                    database, item, soort, methode, score, kandidaat,
                    None, handtekening, nu
                )
            except (SpotifyApiFout, ValueError) as fout:
                soort = "error"
                fouten += 1
                _bewaar_resultaat(
                    database, item, soort, "original", None, None,
                    str(fout), handtekening, nu
                )
                continue
            if soort == "found":
                gevonden += 1
            elif soort == "ambiguous":
                ambiguous += 1
            else:
                niet_gevonden += 1

    playlist_tracks = exporteer_spotify_recovery_playlist(
        database, export_pad
    )
    resultaat = RecoverySpotifyResultaat(
        len(geschikt), gevonden, ambiguous, niet_gevonden, fouten,
        onvoldoende, overgeslagen, playlist_tracks,
        credentials_ontbreken
    )
    toon_spotify_recovery_overzicht(database, resultaat, uitvoer)
    return resultaat


def toon_spotify_recovery_overzicht(database, resultaat, uitvoer=None):
    uitvoer = uitvoer or sys.stdout
    uitvoer.write("\nSPOTIFY RECOVERY\n")
    uitvoer.write(f"Geschikt             : {resultaat.geschikt}\n")
    uitvoer.write(f"Gevonden             : {resultaat.gevonden}\n")
    uitvoer.write(f"Ambiguous            : {resultaat.ambiguous}\n")
    uitvoer.write(f"Niet gevonden        : {resultaat.niet_gevonden}\n")
    uitvoer.write(f"Fouten               : {resultaat.fouten}\n")
    uitvoer.write(
        f"Onvoldoende identiteit: {resultaat.onvoldoende_identiteit}\n"
    )
    uitvoer.write(f"Playlist-tracks      : {resultaat.playlist_tracks}\n")
    rijen = database.verbinding.execute(
        """
        SELECT r.id, r.bepaalde_artiest, r.bepaalde_titel,
               p.gevonden_artiest, p.gevonden_titel, p.matchscore
        FROM recovery_provider_resultaten p
        JOIN recovery_items r ON r.id = p.recovery_item_id
        WHERE p.provider = ? AND p.resultaat_type = 'ambiguous'
        ORDER BY r.id
        """,
        (SPOTIFY_PROVIDER,),
    )
    for rij in rijen:
        uitvoer.write(
            f"- ID {rij['id']}: {rij['bepaalde_artiest']} - "
            f"{rij['bepaalde_titel']} => {rij['gevonden_artiest']} - "
            f"{rij['gevonden_titel']} ({rij['matchscore']:.4f})\n"
        )


def verkrijg_spotify_recovery_overzicht(database):
    """Geef de actuele opgeslagen recovery-Spotifystatus voor rapportage."""

    rij = database.verbinding.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM recovery_items
           WHERE bepaalde_artiest IS NOT NULL
             AND bepaalde_titel IS NOT NULL
             AND identiteit_betrouwbaarheid >= ?) AS geschikt,
          (SELECT COUNT(*) FROM recovery_items
           WHERE bepaalde_artiest IS NULL OR bepaalde_titel IS NULL
              OR identiteit_betrouwbaarheid < ?
              OR identiteit_betrouwbaarheid IS NULL) AS onvoldoende,
          SUM(CASE WHEN resultaat_type = 'found' THEN 1 ELSE 0 END)
            AS gevonden,
          SUM(CASE WHEN resultaat_type = 'ambiguous' THEN 1 ELSE 0 END)
            AS ambiguous,
          SUM(CASE WHEN resultaat_type = 'not_found' THEN 1 ELSE 0 END)
            AS niet_gevonden,
          SUM(CASE WHEN resultaat_type = 'error' THEN 1 ELSE 0 END)
            AS fouten
        FROM recovery_provider_resultaten
        WHERE provider = ?
        """,
        (MINIMALE_IDENTITEIT, MINIMALE_IDENTITEIT, SPOTIFY_PROVIDER),
    ).fetchone()
    return {
        "geschikt": rij["geschikt"] or 0,
        "onvoldoende_identiteit": rij["onvoldoende"] or 0,
        "gevonden": rij["gevonden"] or 0,
        "ambiguous": rij["ambiguous"] or 0,
        "niet_gevonden": rij["niet_gevonden"] or 0,
        "fouten": rij["fouten"] or 0,
        "playlist_tracks": rij["gevonden"] or 0,
    }
