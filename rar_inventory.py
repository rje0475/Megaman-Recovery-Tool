import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from database import begin_rar_inventory_scan
from database import bewaar_rar_set
from database import eindig_rar_inventory_scan
from database import vergelijk_rar_inventory
from database import verkrijg_rar_inventory_overzicht
from database import vervang_rar_inventory_items
from paden import normaliseer_relatief_pad
from paden import normaliseer_relatief_pad_sleutel
from rar import ZEVEN_ZIP
from rar import decodeer_7zip_uitvoer


RAR_VOLUME_PATTERN = re.compile(
    r"^(?P<naam>.+)\.part(?P<deel>\d+)\.rar$",
    re.IGNORECASE
)
INVENTARIS_BRON = "7zip-slt"


@dataclass(frozen=True)
class RarSet:
    rar_set_key: str
    startbestand: Path
    volumes: tuple[Path, ...]


@dataclass(frozen=True)
class RarListingResultaat:
    items: tuple[dict, ...]
    volledig: bool
    fout: str | None = None


def groepeer_rar_sets(rar_map):
    """
    Groepeer multipart RAR-volumes rond ieder bestaand .part01.rar-bestand.
    """

    rar_map = Path(rar_map)
    groepen = {}

    for bestand in rar_map.rglob("*"):
        if not bestand.is_file():
            continue

        match = RAR_VOLUME_PATTERN.match(bestand.name)

        if not match:
            continue

        groepssleutel = (
            normaliseer_relatief_pad_sleutel(
                bestand.parent.relative_to(rar_map)
            )
            if bestand.parent != rar_map
            else ""
        ), match.group("naam").casefold()
        groepen.setdefault(groepssleutel, []).append(
            (
                int(match.group("deel")),
                match.group("deel"),
                bestand
            )
        )

    rar_sets = []

    for volumes in groepen.values():
        startkandidaten = [
            bestand
            for deel, deeltekst, bestand in volumes
            if deel == 1 and deeltekst == "01"
        ]

        if not startkandidaten:
            continue

        startbestand = sorted(
            startkandidaten,
            key=lambda pad: str(pad).casefold()
        )[0]
        relatief_start = normaliseer_relatief_pad(
            startbestand.relative_to(rar_map)
        )
        basis = re.sub(
            r"(?i)\.part01\.rar$",
            "",
            relatief_start
        )
        rar_sets.append(
            RarSet(
                rar_set_key=normaliseer_relatief_pad_sleutel(basis),
                startbestand=startbestand,
                volumes=tuple(
                    bestand
                    for _, _, bestand in sorted(
                        volumes,
                        key=lambda item: (
                            item[0],
                            str(item[2]).casefold()
                        )
                    )
                )
            )
        )

    return sorted(rar_sets, key=lambda rar_set: rar_set.rar_set_key)


def parseer_7zip_listing(uitvoer):
    """
    Parseer MP3-items uit de technische uitvoer van `7z l -slt`.
    """

    regels = uitvoer.splitlines()
    start_index = next(
        (
            index + 1
            for index, regel in enumerate(regels)
            if regel.strip() == "----------"
        ),
        0
    )
    blokken = []
    huidig = {}

    for regel in regels[start_index:]:
        regel = regel.rstrip()

        if not regel.strip():
            if huidig:
                blokken.append(huidig)
                huidig = {}
            continue

        if " = " not in regel:
            continue

        sleutel, waarde = regel.split(" = ", 1)
        huidig[sleutel.strip()] = waarde.strip()

    if huidig:
        blokken.append(huidig)

    items = []

    for blok in blokken:
        intern_pad = blok.get("Path")

        if not intern_pad or not intern_pad.casefold().endswith(".mp3"):
            continue

        try:
            verwacht_rel_pad = normaliseer_relatief_pad(intern_pad)
        except ValueError:
            continue

        windows_pad = PureWindowsPath(verwacht_rel_pad)
        verwachte_map = (
            str(windows_pad.parent)
            if str(windows_pad.parent) != "."
            else ""
        )
        grootte = _naar_int(blok.get("Size"))
        crc = blok.get("CRC") or None
        items.append({
            "verwacht_rel_pad": verwacht_rel_pad,
            "verwacht_rel_pad_norm":
                normaliseer_relatief_pad_sleutel(verwacht_rel_pad),
            "verwachte_map": verwachte_map,
            "verwachte_bestandsnaam": windows_pad.name,
            "verwachte_grootte": grootte,
            "verwachte_crc32": crc.upper() if crc else None,
            "verwachte_modified": blok.get("Modified") or None
        })

    return items


def lees_rar_listing(rar_set, zeven_zip=ZEVEN_ZIP):
    """
    Lees een RAR-set technisch uit zonder bestanden te wijzigen of uit te pakken.
    """

    try:
        resultaat = subprocess.run(
            [
                zeven_zip,
                "l",
                "-slt",
                str(rar_set.startbestand)
            ],
            capture_output=True,
            timeout=120
        )
    except (OSError, subprocess.TimeoutExpired) as fout:
        return RarListingResultaat(
            items=(),
            volledig=False,
            fout=str(fout)
        )

    standaard = decodeer_7zip_uitvoer(resultaat.stdout)
    fouten = decodeer_7zip_uitvoer(resultaat.stderr)
    items = tuple(parseer_7zip_listing(standaard))
    volledig = resultaat.returncode == 0
    fout = None

    if not volledig:
        fout = _beperk_foutmelding(fouten or standaard, resultaat.returncode)

    return RarListingResultaat(
        items=items,
        volledig=volledig,
        fout=fout
    )


def voer_rar_inventory_uit(
    rar_map,
    database,
    uitvoer=None,
    listing_lezer=None,
    vergelijk=True,
):
    """
    Inventariseer alle RAR-sets en vergelijk ze met actieve MP3-records.
    """

    uitvoer = uitvoer or sys.stdout
    listing_lezer = listing_lezer or lees_rar_listing
    rar_sets = groepeer_rar_sets(rar_map)
    begin_rar_inventory_scan(database)

    for rar_set in rar_sets:
        try:
            listing = listing_lezer(rar_set)
        except Exception as fout:
            listing = RarListingResultaat(
                items=(),
                volledig=False,
                fout=str(fout)
            )

        bewaar_rar_set(
            database,
            rar_set.rar_set_key,
            rar_set.startbestand,
            listing.volledig,
            listing.fout,
            INVENTARIS_BRON
        )
        vervang_rar_inventory_items(
            database,
            rar_set.rar_set_key,
            rar_set.startbestand,
            listing.items,
            listing.fout,
            INVENTARIS_BRON
        )

        if listing.fout:
            uitvoer.write(
                f"RAR-listing onvolledig [{rar_set.rar_set_key}]: "
                f"{listing.fout}\n"
            )

    eindig_rar_inventory_scan(database)
    if vergelijk:
        vergelijk_rar_inventory(database)
    overzicht = verkrijg_rar_inventory_overzicht(database)
    toon_rar_inventory_overzicht(overzicht, uitvoer)
    return overzicht


def toon_rar_inventory_overzicht(overzicht, uitvoer=None):
    """
    Toon de inventaristellingen.
    """

    uitvoer = uitvoer or sys.stdout
    uitvoer.write("\nRAR-INVENTARIS\n")
    uitvoer.write(f"RAR-sets                 : {overzicht['rar_sets']}\n")
    uitvoer.write(
        f"Verwachte MP3's          : {overzicht['verwachte_mp3s']}\n"
    )
    uitvoer.write(
        f"Aangetroffen MP3's       : {overzicht['aangetroffen_mp3s']}\n"
    )
    uitvoer.write(
        f"Ontbrekende MP3's        : {overzicht['ontbrekende_mp3s']}\n"
    )
    uitvoer.write(
        f"Afwijkende grootte       : {overzicht['grootte_afwijkend']}\n"
    )
    uitvoer.write(
        f"Onvolledige RAR-listings : {overzicht['listing_fouten']}\n"
    )


def _naar_int(waarde):
    try:
        return int(waarde)
    except (TypeError, ValueError):
        return None


def _beperk_foutmelding(uitvoer, returncode):
    regels = [
        regel.strip()
        for regel in uitvoer.splitlines()
        if regel.strip()
    ]

    if not regels:
        return f"7-Zip eindigde met code {returncode}."

    return " | ".join(regels[-5:])
