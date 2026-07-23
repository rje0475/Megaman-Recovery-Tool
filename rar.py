import subprocess

from database import zet_rar_status
from paden import normaliseer_relatief_pad

# Pad naar 7-Zip
ZEVEN_ZIP = r"C:\Program Files\7-Zip\7z.exe"


def zoek_part01_bestanden(rar_map):
    """
    Zoek alle .part01.rar bestanden.
    """

    return sorted(rar_map.rglob("*.part01.rar"))


def normaliseer_pad(bestandnaam):
    r"""
    Zet een pad uit de RAR om naar hetzelfde formaat als de database.
    """

    return normaliseer_relatief_pad(bestandnaam)


def decodeer_7zip_uitvoer(data):
    """
    Decodeer de uitvoer van 7-Zip.

    7-Zip schrijft op Windows niet altijd UTF-8 of de actieve ANSI-codepagina.
    Daarom proberen we meerdere decoders.
    """

    for encoding in ("cp850", "cp437", "cp1252", "utf-8"):

        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass

    return data.decode(errors="replace")


def test_rar(bestand, database):
    """
    Test één RAR-set met 7-Zip.

    Geeft terug:
        ok
        fouten
    """

    resultaat = subprocess.run(
        [ZEVEN_ZIP, "t", str(bestand)],
        capture_output=True
    )

    uitvoer = (
        decodeer_7zip_uitvoer(resultaat.stdout)
        + decodeer_7zip_uitvoer(resultaat.stderr)
    )

    fouten = []

    for regel in uitvoer.splitlines():

        regel = regel.strip()

        if regel.startswith("ERROR: Data Error :"):

            bestandnaam = regel.replace("ERROR: Data Error :", "").strip()
            relatief_pad = normaliseer_pad(bestandnaam)

            if relatief_pad not in database:
                print(f"NIET GEVONDEN: {bestandnaam}")
                print(f"GENORMALISEERD: {relatief_pad}")
                print()

            fouten.append({
                "bestand": relatief_pad,
                "fout": "Data Error"
            })

            zet_rar_status(
                database,
                relatief_pad,
                "ERROR",
                "Data Error"
            )

        elif regel.startswith("ERROR: CRC Failed :"):

            bestandnaam = regel.replace("ERROR: CRC Failed :", "").strip()
            relatief_pad = normaliseer_pad(bestandnaam)

            if relatief_pad not in database:
                print(f"NIET GEVONDEN: {bestandnaam}")
                print(f"GENORMALISEERD: {relatief_pad}")
                print()

            fouten.append({
                "bestand": relatief_pad,
                "fout": "CRC Failed"
            })

            zet_rar_status(
                database,
                relatief_pad,
                "ERROR",
                "CRC Failed"
            )

    ok = len(fouten) == 0

    return ok, fouten
