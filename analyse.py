import sys
from dataclasses import dataclass
from pathlib import Path

from database import (
    DATABASE_BESTAND,
    maak_database,
    vergelijk_rar_inventory,
)
from identity import bepaal_recovery_identiteiten
from par_inventory import voer_par_inventory_uit
from rar import ZEVEN_ZIP, zoek_part01_bestanden, test_rar
from rar_inventory import voer_rar_inventory_uit
from recovery import genereer_recovery_items
from report import maak_rapport
from scanner import FFMPEG, controleer_mp3_bestanden, zoek_mp3_bestanden
from spotify import voer_spotify_scan_uit
from spotify_recovery import voer_spotify_recovery_uit


class AnalyseFout(RuntimeError):
    """Een duidelijke, verwachte analysefout voor de CLI."""


@dataclass(frozen=True)
class AnalyseResultaat:
    database_pad: Path
    rapport_pad: Path
    totaal_mp3: int
    goed: int
    nul_bytes: int
    rar_fouten: int
    ffmpeg_fouten: int


def _controleer_hulpprogrammas(mp3_bestanden, rar_bestanden):
    ontbrekend = []
    if mp3_bestanden and not Path(FFMPEG).is_file():
        ontbrekend.append(
            f"FFmpeg ontbreekt op de verwachte locatie: {FFMPEG}"
        )
    if rar_bestanden and not Path(ZEVEN_ZIP).is_file():
        ontbrekend.append(
            f"7-Zip ontbreekt op de verwachte locatie: {ZEVEN_ZIP}"
        )
    if ontbrekend:
        raise AnalyseFout("\n".join(ontbrekend))


def voer_analyse(
    mp3_map,
    rar_map=None,
    database_pad=DATABASE_BESTAND,
    uitvoer=None,
):
    """Voer de volledige bestaande read-only analysekern één keer uit."""

    uitvoer = uitvoer or sys.stdout
    mp3_map = Path(mp3_map)
    rar_map = Path(rar_map) if rar_map is not None else mp3_map
    if not mp3_map.is_dir():
        raise AnalyseFout(f"MP3-map bestaat niet: {mp3_map}")
    if not rar_map.is_dir():
        raise AnalyseFout(f"RAR/PAR-map bestaat niet: {rar_map}")

    mp3_bestanden = zoek_mp3_bestanden(mp3_map)
    rar_bestanden = zoek_part01_bestanden(rar_map)
    _controleer_hulpprogrammas(mp3_bestanden, rar_bestanden)

    database = maak_database(database_pad)
    try:
        uitvoer.write("\nMP3's scannen...\n")
        controleer_mp3_bestanden(mp3_bestanden, mp3_map, database)

        uitvoer.write("\nRAR-inventaris uitlezen...\n")
        voer_rar_inventory_uit(
            rar_map, database, uitvoer=uitvoer, vergelijk=False
        )
        voer_par_inventory_uit(rar_map, database, uitvoer=uitvoer)
        vergelijk_rar_inventory(database)

        uitvoer.write("\nRAR-sets controleren...\n")
        for rar in rar_bestanden:
            try:
                test_rar(rar, database)
            except OSError as fout:
                uitvoer.write(
                    f"RAR-controle overgeslagen voor {rar}: {fout}\n"
                )

        genereer_recovery_items(database, uitvoer=uitvoer)
        bepaal_recovery_identiteiten(database, uitvoer=uitvoer)

        uitvoer.write("\nSpotify-verrijking...\n")
        voer_spotify_scan_uit(database, uitvoer=uitvoer)
        voer_spotify_recovery_uit(database, uitvoer=uitvoer)

        totaal = len(database)
        goed = sum(
            1 for gegevens in database.values()
            if not gegevens["nul_bytes"]
            and gegevens["rar"]["status"] != "ERROR"
            and gegevens["ffmpeg"]["status"] != "ERROR"
        )
        nul_bytes = sum(
            1 for gegevens in database.values() if gegevens["nul_bytes"]
        )
        rar_fouten = sum(
            1 for gegevens in database.values()
            if gegevens["rar"]["status"] == "ERROR"
        )
        ffmpeg_fouten = sum(
            1 for gegevens in database.values()
            if gegevens["ffmpeg"]["status"] == "ERROR"
        )
        _toon_resultaat(
            uitvoer, database, totaal, goed, nul_bytes,
            rar_fouten, ffmpeg_fouten
        )
        rapport = maak_rapport(mp3_map, database)
        uitvoer.write(f"\nRapport opgeslagen: {rapport}\n")
        return AnalyseResultaat(
            database_pad=Path(database_pad),
            rapport_pad=rapport,
            totaal_mp3=totaal,
            goed=goed,
            nul_bytes=nul_bytes,
            rar_fouten=rar_fouten,
            ffmpeg_fouten=ffmpeg_fouten,
        )
    finally:
        database.sluit()


def _toon_resultaat(
    uitvoer, database, totaal, goed, nul_bytes,
    rar_fouten, ffmpeg_fouten
):
    uitvoer.write("\n===================================\n")
    uitvoer.write("SCAN RESULTAAT\n")
    uitvoer.write("===================================\n")
    uitvoer.write(f"MP3 bestanden      : {totaal}\n")
    uitvoer.write(f"Bestanden OK       : {goed}\n")
    uitvoer.write(f"0-byte bestanden   : {nul_bytes}\n")
    uitvoer.write(f"RAR fouten         : {rar_fouten}\n")
    uitvoer.write(f"FFmpeg fouten      : {ffmpeg_fouten}\n")
    for gegevens in database.values():
        if gegevens["nul_bytes"]:
            uitvoer.write(f"0-byte: {gegevens['relatief_pad']}\n")
        if gegevens["rar"]["status"] == "ERROR":
            uitvoer.write(
                f"RAR [{gegevens['rar']['type']}]: "
                f"{gegevens['relatief_pad']}\n"
            )
        if gegevens["ffmpeg"]["status"] == "ERROR":
            uitvoer.write(
                f"FFmpeg [{gegevens['ffmpeg']['type']}]: "
                f"{gegevens['relatief_pad']}\n"
            )
