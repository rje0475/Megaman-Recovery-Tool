import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath

from paden import normaliseer_relatief_pad


TECHNISCHE_HAAKJES = re.compile(
    r"(?ix)"
    r"[\(\[]\s*(?:"
    r"official\s+(?:music\s+)?(?:video|audio)|"
    r"lyric(?:s|\s+video)?|"
    r"hd|hq|"
    r"(?:19|20)\d{2}\s+(?:re)?master(?:ed)?|"
    r"(?:re)?master(?:ed)?(?:\s+(?:19|20)\d{2})?|"
    r"\d{2,3}\s*kbps"
    r")\s*[\)\]]"
)
LOSSE_TECHNISCHE_RUIS = re.compile(
    r"(?ix)(?:^|[\s_-]+)"
    r"(?:official\s+(?:music\s+)?(?:video|audio)|"
    r"lyric(?:s|\s+video)?|hd|hq|"
    r"(?:19|20)\d{2}\s+(?:re)?master(?:ed)?|"
    r"(?:re)?master(?:ed)?(?:\s+(?:19|20)\d{2})?|"
    r"\d{2,3}\s*kbps)"
    r"(?=$|[\s_-]+)"
)
TRACKNUMMER = re.compile(
    r"^\s*(?P<track>\d{1,3})(?:\s*[-._]\s*|\s+)"
)


@dataclass(frozen=True)
class Identiteit:
    artiest: str | None = None
    titel: str | None = None
    album: str | None = None
    tracknummer: str | None = None
    bron: str | None = None
    betrouwbaarheid: float = 0.0
    reden: str | None = None


def schoon_technische_ruis(waarde):
    """Verwijder alleen herkenbare publicatie- en encodingruis."""

    if not waarde:
        return None
    tekst = TECHNISCHE_HAAKJES.sub(" ", str(waarde))
    tekst = LOSSE_TECHNISCHE_RUIS.sub(" ", tekst)
    tekst = re.sub(r"\s+", " ", tekst).strip(" _-.")
    return tekst or None


def parseer_verwacht_pad(relatief_pad, verwachte_bestandsnaam=None):
    """Leid conservatief identiteit af uit een verwacht Windows-pad."""

    genormaliseerd = normaliseer_relatief_pad(relatief_pad)
    pad = PureWindowsPath(genormaliseerd)
    bestandsnaam = verwachte_bestandsnaam or pad.name
    stam = re.sub(r"(?i)\.mp3$", "", bestandsnaam).strip()
    track_match = TRACKNUMMER.match(stam)
    tracknummer = track_match.group("track") if track_match else None

    if track_match:
        stam = stam[track_match.end():]
    elif stam.isdigit():
        tracknummer = stam
        stam = ""

    stam = re.sub(r"_+\s*-\s*_+", " - ", stam)
    stam = re.sub(r"\s+-\s+", " - ", stam)
    stam = schoon_technische_ruis(stam)

    if not stam:
        return Identiteit(
            tracknummer=tracknummer,
            bron="bestandsnaam",
            reden="geen bruikbare tekst na opschoning",
        )

    artiest = None
    titel = None
    betrouwbaarheid = 0.4

    if " - " in stam:
        artiest, titel = (
            schoon_technische_ruis(deel)
            for deel in stam.split(" - ", 1)
        )
        betrouwbaarheid = 0.78 if artiest and titel else 0.4
    else:
        titel = stam

    mapdelen = list(pad.parts[:-1])
    album = schoon_technische_ruis(mapdelen[-1]) if mapdelen else None

    if not artiest and len(mapdelen) >= 2:
        artiest = schoon_technische_ruis(mapdelen[-2])
        betrouwbaarheid = 0.65 if artiest and titel else betrouwbaarheid

    return Identiteit(
        artiest=artiest,
        titel=titel,
        album=album,
        tracknummer=tracknummer,
        bron="bestandsnaam_en_pad",
        betrouwbaarheid=betrouwbaarheid,
        reden=None if titel else "titel niet herkenbaar",
    )


def lees_mp3_metadata(bestand):
    """Lees gangbare MP3-tags met de kleine Mutagen-library."""

    try:
        from mutagen import File
    except ImportError:
        return None

    try:
        tags = File(str(bestand), easy=True)
    except Exception:
        return None

    if tags is None:
        return None

    def eerste(sleutel):
        waarden = tags.get(sleutel)
        return schoon_technische_ruis(waarden[0]) if waarden else None

    tracknummer = eerste("tracknumber")
    if tracknummer:
        tracknummer = tracknummer.split("/", 1)[0].strip() or None

    identiteit = Identiteit(
        artiest=eerste("artist"),
        titel=eerste("title"),
        album=eerste("album"),
        tracknummer=tracknummer,
        bron="mp3_metadata",
        betrouwbaarheid=0.95,
    )
    return identiteit if identiteit.artiest or identiteit.titel else None


def _spotify_identiteit(database, relatief_pad):
    if not relatief_pad:
        return None

    rij = database.verbinding.execute(
        """
        SELECT artiest, titel, album
        FROM provider_resultaten
        WHERE relatief_pad = ?
          AND provider = 'spotify'
          AND gevonden = 1
          AND track_id IS NOT NULL
          AND artiest IS NOT NULL
          AND titel IS NOT NULL
        """,
        (relatief_pad,),
    ).fetchone()
    if rij is None:
        return None

    return Identiteit(
        artiest=rij["artiest"],
        titel=rij["titel"],
        album=rij["album"],
        bron="spotify_bestaand",
        betrouwbaarheid=0.98,
    )


def _brondata(database, item):
    mp3 = None
    inventaris = None

    if item["mp3_id"] is not None:
        mp3 = database.verbinding.execute(
            "SELECT * FROM mp3_bestanden WHERE id = ?",
            (item["mp3_id"],),
        ).fetchone()
    if item["inventaris_id"] is not None:
        inventaris = database.verbinding.execute(
            "SELECT * FROM rar_inventory_items WHERE id = ?",
            (item["inventaris_id"],),
        ).fetchone()

    bestand = Path(mp3["bestand"]) if mp3 is not None else None
    stat = None
    if bestand is not None:
        try:
            bestand_stat = bestand.stat()
            stat = [bestand_stat.st_size, bestand_stat.st_mtime_ns]
        except OSError:
            pass

    relatief_pad = (
        mp3["relatief_pad"]
        if mp3 is not None
        else item["verwacht_rel_pad"]
    )
    spotify = database.verbinding.execute(
        """
        SELECT track_id, artiest, titel, album, gevonden
        FROM provider_resultaten
        WHERE relatief_pad = ? AND provider = 'spotify'
        """,
        (relatief_pad,),
    ).fetchone()
    data = {
        "pad": item["verwacht_rel_pad"],
        "bestandsnaam": (
            inventaris["verwachte_bestandsnaam"]
            if inventaris is not None
            else PureWindowsPath(item["verwacht_rel_pad"]).name
        ),
        "mp3": [mp3["id"], str(bestand), stat] if mp3 is not None else None,
        "spotify": dict(spotify) if spotify is not None else None,
    }
    handtekening = hashlib.sha256(
        json.dumps(data, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return mp3, data["bestandsnaam"], relatief_pad, handtekening


def _kies_identiteit(database, item, metadata_lezer):
    mp3, bestandsnaam, relatief_pad, handtekening = _brondata(database, item)
    metadata = None

    if mp3 is not None and mp3["bestaat"]:
        metadata = metadata_lezer(Path(mp3["bestand"]))
        if metadata and metadata.artiest and metadata.titel:
            return metadata, handtekening

    uit_pad = parseer_verwacht_pad(
        item["verwacht_rel_pad"], bestandsnaam
    )
    if (
        uit_pad.artiest
        and uit_pad.titel
        and uit_pad.betrouwbaarheid >= 0.75
    ):
        return uit_pad, handtekening

    spotify = _spotify_identiteit(database, relatief_pad)
    if spotify:
        return spotify, handtekening

    bruikbaar = [
        kandidaat
        for kandidaat in (metadata, uit_pad)
        if kandidaat is not None
        if kandidaat.artiest or kandidaat.titel
    ]
    gekozen = max(
        bruikbaar,
        key=lambda kandidaat: kandidaat.betrouwbaarheid,
        default=Identiteit(
            bron="niet_herkend",
            reden="geen bruikbare metadata, bestandsnaam of koppeling",
        ),
    )
    return gekozen, handtekening


def bepaal_recovery_identiteiten(database, metadata_lezer=None, uitvoer=None):
    """Vul ontbrekende of gewijzigde recovery-identiteiten incrementeel."""

    metadata_lezer = metadata_lezer or lees_mp3_metadata
    uitvoer = uitvoer or sys.stdout
    items = [
        dict(rij)
        for rij in database.verbinding.execute(
            "SELECT * FROM recovery_items ORDER BY id"
        )
    ]
    nu = datetime.now().isoformat(timespec="seconds")

    for item in items:
        gekozen, handtekening = _kies_identiteit(
            database, item, metadata_lezer
        )
        bestaand_betrouwbaar = item["identiteit_betrouwbaarheid"] or 0.0
        ongewijzigd = (
            item["identiteit_bron_handtekening"] == handtekening
            and item["identiteit_bron"] is not None
        )
        handmatig = item["identiteit_bron"] == "handmatig"

        if ongewijzigd or handmatig:
            continue
        if (
            bestaand_betrouwbaar > gekozen.betrouwbaarheid
            and (item["bepaalde_artiest"] or item["bepaalde_titel"])
        ):
            continue

        database.verbinding.execute(
            """
            UPDATE recovery_items
            SET bepaalde_artiest = ?,
                bepaalde_titel = ?,
                bepaald_album = ?,
                bepaald_tracknummer = ?,
                identiteit_bron = ?,
                identiteit_betrouwbaarheid = ?,
                identiteit_bepaald_op = ?,
                identiteit_bron_handtekening = ?,
                identiteit_reden = ?
            WHERE id = ?
            """,
            (
                gekozen.artiest,
                gekozen.titel,
                gekozen.album,
                gekozen.tracknummer,
                gekozen.bron,
                gekozen.betrouwbaarheid,
                nu,
                handtekening,
                gekozen.reden,
                item["id"],
            ),
        )

    database.verbinding.commit()
    overzicht = verkrijg_identiteit_overzicht(database)
    toon_identiteit_overzicht(database, overzicht, uitvoer)
    return overzicht


def verkrijg_identiteit_overzicht(database):
    totalen = database.verbinding.execute(
        """
        SELECT
          SUM(CASE WHEN bepaalde_artiest IS NOT NULL
                        AND bepaalde_titel IS NOT NULL THEN 1 ELSE 0 END)
            AS artiest_en_titel,
          SUM(CASE WHEN bepaalde_artiest IS NULL
                        AND bepaalde_titel IS NOT NULL THEN 1 ELSE 0 END)
            AS alleen_titel,
          SUM(CASE WHEN bepaalde_titel IS NULL THEN 1 ELSE 0 END)
            AS zonder_identiteit
        FROM recovery_items
        """
    ).fetchone()
    bronnen = database.verbinding.execute(
        """
        SELECT COALESCE(identiteit_bron, 'niet_bepaald') AS bron,
               COUNT(*) AS aantal
        FROM recovery_items
        GROUP BY COALESCE(identiteit_bron, 'niet_bepaald')
        ORDER BY bron
        """
    ).fetchall()
    return {
        "artiest_en_titel": totalen["artiest_en_titel"] or 0,
        "alleen_titel": totalen["alleen_titel"] or 0,
        "zonder_identiteit": totalen["zonder_identiteit"] or 0,
        "bronnen": {rij["bron"]: rij["aantal"] for rij in bronnen},
    }


def toon_identiteit_overzicht(database, overzicht, uitvoer=None):
    uitvoer = uitvoer or sys.stdout
    uitvoer.write("\nRECOVERY-IDENTITEIT\n")
    uitvoer.write(
        f"Artiest en titel : {overzicht['artiest_en_titel']}\n"
    )
    uitvoer.write(f"Alleen titel     : {overzicht['alleen_titel']}\n")
    uitvoer.write(
        f"Zonder identiteit: {overzicht['zonder_identiteit']}\n"
    )
    for bron, aantal in overzicht["bronnen"].items():
        uitvoer.write(f"- {bron}: {aantal}\n")

    onherkend = database.verbinding.execute(
        """
        SELECT id, rar_set_key, verwacht_rel_pad, identiteit_bron,
               identiteit_reden
        FROM recovery_items
        WHERE bepaalde_titel IS NULL
        ORDER BY id
        """
    )
    for item in onherkend:
        reden = (
            item["identiteit_reden"]
            or item["identiteit_bron"]
            or "nog niet bepaald"
        )
        uitvoer.write(
            f"- ID {item['id']} [{item['rar_set_key']}] "
            f"{item['verwacht_rel_pad']} "
            f"({reden})\n"
        )
