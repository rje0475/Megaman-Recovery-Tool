import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from database import voeg_mp3_toe
from database import zet_ffmpeg_status
from database import zet_nul_bytes


FFMPEG = r"C:\ffmpeg\ffmpeg.exe"

AANTAL_THREADS = 2
BREEDTE_VOORTGANGSBALK = 30


def zoek_mp3_bestanden(map_pad):
    """
    Zoek alle MP3-bestanden.
    """
    return sorted(map_pad.rglob("*.mp3"))


def bepaal_ffmpeg_type(melding):
    """
    Vertaal een FFmpeg-melding naar een herkenbaar fouttype.
    """
    tekst = melding.lower()

    if "header missing" in tekst:
        return "Header missing"

    if "invalid frame size" in tekst:
        return "Invalid frame size"

    if "failed to read frame size" in tekst:
        return "Frame size"

    if "error while decoding" in tekst:
        return "Decode error"

    if "invalid data found" in tekst:
        return "Invalid data"

    if "end of file" in tekst:
        return "Unexpected EOF"

    return "Unknown"


def schoon_ffmpeg_melding(melding):
    """
    Maak FFmpeg-uitvoer leesbaar.
    """

    unieke_regels = []
    gezien = set()

    for regel in melding.splitlines():

        regel = regel.strip()

        if not regel:
            continue

        # Verwijder alles tussen [ ] aan het begin van de regel
        regel = re.sub(r"(\[[^\]]+\]\s*)+", "", regel)

        # Verwijder dubbele spaties
        regel = re.sub(r"\s+", " ", regel).strip()

        if not regel:
            continue

        if regel in gezien:
            continue

        gezien.add(regel)
        unieke_regels.append(regel)

    return "\n".join(unieke_regels)


def controleer_bestand(bestand, basis_map):
    """
    Controleer één MP3 met FFmpeg.
    """

    relatief_pad = str(bestand.relative_to(basis_map))

    if bestand.stat().st_size == 0:
        return relatief_pad, "ZERO", None, None

    try:

        resultaat = subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-i",
                str(bestand),
                "-f",
                "null",
                "-"
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30
        )

    except subprocess.TimeoutExpired:

        return relatief_pad, "ERROR", "Timeout", "FFmpeg timeout na 30 seconden."

    melding = resultaat.stderr.strip()

    if melding:

        melding = schoon_ffmpeg_melding(melding)

        return (
            relatief_pad,
            "ERROR",
            bepaal_ffmpeg_type(melding),
            melding
        )

    return relatief_pad, "OK", None, None


def toon_voortgang(verwerkt, totaal, goed, ffmpeg_fouten, nul_bytes):
    """
    Toon de actuele scanstatus op één consoleregel.
    """

    percentage = 100 if totaal == 0 else int(verwerkt / totaal * 100)
    gevuld = int(BREEDTE_VOORTGANGSBALK * percentage / 100)
    balk = "#" * gevuld + "-" * (BREEDTE_VOORTGANGSBALK - gevuld)

    sys.stdout.write(
        f"\r[{balk}] {percentage:3d}% "
        f"{verwerkt}/{totaal} | OK: {goed} | "
        f"FFmpeg-fouten: {ffmpeg_fouten} | 0-byte: {nul_bytes}"
    )
    sys.stdout.flush()


def controleer_mp3_bestanden(mp3_bestanden, basis_map, database):
    """
    Controleer alle MP3-bestanden parallel.
    """

    goed = 0
    nul_bytes = []
    ffmpeg_fouten = 0

    for bestand in mp3_bestanden:
        voeg_mp3_toe(database, basis_map, bestand)

    totaal = len(mp3_bestanden)
    verwerkt = 0

    toon_voortgang(verwerkt, totaal, goed, ffmpeg_fouten, len(nul_bytes))

    with ThreadPoolExecutor(max_workers=AANTAL_THREADS) as executor:

        futures = {
            executor.submit(controleer_bestand, bestand, basis_map): bestand
            for bestand in mp3_bestanden
        }

        for future in as_completed(futures):

            bestand = futures[future]

            relatief_pad, status, fouttype, melding = future.result()

            if status == "ZERO":

                zet_nul_bytes(database, basis_map, bestand)
                nul_bytes.append(bestand)

            elif status == "ERROR":

                zet_ffmpeg_status(
                    database,
                    relatief_pad,
                    "ERROR",
                    fouttype,
                    melding
                )
                ffmpeg_fouten += 1

            else:

                zet_ffmpeg_status(
                    database,
                    relatief_pad,
                    "OK",
                    None,
                    None
                )

                goed += 1

            verwerkt += 1
            toon_voortgang(
                verwerkt,
                totaal,
                goed,
                ffmpeg_fouten,
                len(nul_bytes)
            )

    sys.stdout.write("\n")

    return goed, nul_bytes
