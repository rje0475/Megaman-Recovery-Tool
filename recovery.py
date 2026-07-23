import sys
from datetime import datetime

from database import verkrijg_recovery_items
from database import verkrijg_recovery_overzicht
from paden import normaliseer_relatief_pad
from paden import normaliseer_relatief_pad_sleutel


RAR_SET_ZONDER_INVENTARIS = "__zonder_rar_set__"
PROBLEEM_PRIORITEIT = (
    ("ontbreekt", "feit_ontbreekt"),
    ("rar_crc", "feit_rar_crc"),
    ("corrupt", "feit_corrupt"),
    ("nul_bytes", "feit_nul_bytes"),
    ("grootte_afwijking", "feit_grootte_afwijking"),
)


def genereer_recovery_items(database, uitvoer=None):
    """
    Leid de actuele herstelkandidaten af uit inventaris- en scanfeiten.
    """

    uitvoer = uitvoer or sys.stdout
    mp3_rijen = database.verbinding.execute(
        """
        SELECT *
        FROM mp3_bestanden
        WHERE bestaat = 1
        """
    ).fetchall()
    mp3_op_pad = {
        normaliseer_relatief_pad_sleutel(rij["relatief_pad"]): rij
        for rij in mp3_rijen
    }
    inventaris_rijen = database.verbinding.execute(
        """
        SELECT inventaris.*
        FROM rar_inventory_items AS inventaris
        JOIN rar_sets AS sets
          ON sets.rar_set_key = inventaris.rar_set_key
        WHERE sets.actief = 1
        """
    ).fetchall()
    kandidaten = {}
    gekoppelde_mp3_ids = set()

    for inventaris in inventaris_rijen:
        mp3 = mp3_op_pad.get(inventaris["verwacht_rel_pad_norm"])

        if mp3 is not None:
            gekoppelde_mp3_ids.add(mp3["id"])

        kandidaat = _maak_kandidaat(
            rar_set_key=inventaris["rar_set_key"],
            verwacht_rel_pad=inventaris["verwacht_rel_pad"],
            verwacht_rel_pad_norm=inventaris["verwacht_rel_pad_norm"],
            verwachte_grootte=inventaris["verwachte_grootte"],
            verwachte_crc32=inventaris["verwachte_crc32"],
            inventaris_id=inventaris["id"],
            mp3=mp3,
            ontbreekt=bool(inventaris["ontbreekt"]),
            grootte_afwijkend=bool(inventaris["grootte_afwijkend"])
        )

        if kandidaat:
            kandidaten[
                (
                    kandidaat["rar_set_key"],
                    kandidaat["verwacht_rel_pad_norm"]
                )
            ] = kandidaat

    for mp3 in mp3_rijen:
        if mp3["id"] in gekoppelde_mp3_ids:
            continue

        verwacht_rel_pad = normaliseer_relatief_pad(mp3["relatief_pad"])
        kandidaat = _maak_kandidaat(
            rar_set_key=RAR_SET_ZONDER_INVENTARIS,
            verwacht_rel_pad=verwacht_rel_pad,
            verwacht_rel_pad_norm=
                normaliseer_relatief_pad_sleutel(verwacht_rel_pad),
            verwachte_grootte=None,
            verwachte_crc32=None,
            inventaris_id=None,
            mp3=mp3,
            ontbreekt=False,
            grootte_afwijkend=False
        )

        if kandidaat:
            kandidaten[
                (
                    kandidaat["rar_set_key"],
                    kandidaat["verwacht_rel_pad_norm"]
                )
            ] = kandidaat

    _synchroniseer_recovery_items(database, kandidaten)
    overzicht = verkrijg_recovery_overzicht(database)
    toon_recovery_overzicht(
        overzicht,
        verkrijg_recovery_items(database),
        uitvoer
    )
    return overzicht


def _maak_kandidaat(
    rar_set_key,
    verwacht_rel_pad,
    verwacht_rel_pad_norm,
    verwachte_grootte,
    verwachte_crc32,
    inventaris_id,
    mp3,
    ontbreekt,
    grootte_afwijkend
):
    nul_bytes = bool(mp3["nul_bytes"]) if mp3 is not None else False
    corrupt = (
        mp3 is not None
        and mp3["ffmpeg_status"] == "ERROR"
    )
    rar_crc = (
        mp3 is not None
        and mp3["rar_status"] == "ERROR"
        and mp3["rar_type"] in ("CRC Failed", "Data Error")
    )
    feiten = {
        "feit_ontbreekt": ontbreekt,
        "feit_rar_crc": rar_crc,
        "feit_corrupt": corrupt,
        "feit_nul_bytes": nul_bytes,
        "feit_grootte_afwijking": grootte_afwijkend,
    }

    if not any(feiten.values()):
        return None

    probleem_type = next(
        probleem
        for probleem, feit in PROBLEEM_PRIORITEIT
        if feiten[feit]
    )
    bronnen = []

    if ontbreekt or grootte_afwijkend:
        bronnen.append("rar_inventory")

    if rar_crc:
        bronnen.append("rar_test")

    if corrupt:
        bronnen.append("ffmpeg")

    if nul_bytes:
        bronnen.append("bestandsscan")

    return {
        "rar_set_key": rar_set_key,
        "verwacht_rel_pad": verwacht_rel_pad,
        "verwacht_rel_pad_norm": verwacht_rel_pad_norm,
        "probleem_type": probleem_type,
        "probleem_bron": ",".join(bronnen),
        "verwachte_grootte": verwachte_grootte,
        "verwachte_crc32": verwachte_crc32,
        "mp3_id": mp3["id"] if mp3 is not None else None,
        "inventaris_id": inventaris_id,
        "ffmpeg_fout": _ffmpeg_fout(mp3) if corrupt else None,
        "rar_fout": mp3["rar_type"] if rar_crc else None,
        **feiten
    }


def _ffmpeg_fout(mp3):
    onderdelen = [
        waarde
        for waarde in (
            mp3["ffmpeg_type"],
            mp3["ffmpeg_melding"]
        )
        if waarde
    ]
    return ": ".join(onderdelen) or "Onbekende FFmpeg-fout"


def _synchroniseer_recovery_items(database, kandidaten):
    nu = datetime.now().isoformat(timespec="seconds")

    for kandidaat in kandidaten.values():
        database.verbinding.execute(
            """
            INSERT INTO recovery_items (
                rar_set_key,
                verwacht_rel_pad,
                verwacht_rel_pad_norm,
                probleem_type,
                probleem_bron,
                verwachte_grootte,
                verwachte_crc32,
                mp3_id,
                inventaris_id,
                ffmpeg_fout,
                rar_fout,
                feit_ontbreekt,
                feit_rar_crc,
                feit_corrupt,
                feit_nul_bytes,
                feit_grootte_afwijking,
                spotify_verwerkt,
                download_verwerkt,
                geplaatst,
                aangemaakt_op,
                bijgewerkt_op
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    0, 0, 0, ?, ?)
            ON CONFLICT (rar_set_key, verwacht_rel_pad_norm) DO UPDATE SET
                verwacht_rel_pad = excluded.verwacht_rel_pad,
                probleem_type = excluded.probleem_type,
                probleem_bron = excluded.probleem_bron,
                verwachte_grootte = excluded.verwachte_grootte,
                verwachte_crc32 = excluded.verwachte_crc32,
                mp3_id = excluded.mp3_id,
                inventaris_id = excluded.inventaris_id,
                ffmpeg_fout = excluded.ffmpeg_fout,
                rar_fout = excluded.rar_fout,
                feit_ontbreekt = excluded.feit_ontbreekt,
                feit_rar_crc = excluded.feit_rar_crc,
                feit_corrupt = excluded.feit_corrupt,
                feit_nul_bytes = excluded.feit_nul_bytes,
                feit_grootte_afwijking =
                    excluded.feit_grootte_afwijking,
                bijgewerkt_op = excluded.bijgewerkt_op
            """,
            (
                kandidaat["rar_set_key"],
                kandidaat["verwacht_rel_pad"],
                kandidaat["verwacht_rel_pad_norm"],
                kandidaat["probleem_type"],
                kandidaat["probleem_bron"],
                kandidaat["verwachte_grootte"],
                kandidaat["verwachte_crc32"],
                kandidaat["mp3_id"],
                kandidaat["inventaris_id"],
                kandidaat["ffmpeg_fout"],
                kandidaat["rar_fout"],
                kandidaat["feit_ontbreekt"],
                kandidaat["feit_rar_crc"],
                kandidaat["feit_corrupt"],
                kandidaat["feit_nul_bytes"],
                kandidaat["feit_grootte_afwijking"],
                nu,
                nu
            )
        )

    bestaande_sleutels = {
        (rij["rar_set_key"], rij["verwacht_rel_pad_norm"])
        for rij in database.verbinding.execute(
            """
            SELECT rar_set_key, verwacht_rel_pad_norm
            FROM recovery_items
            """
        )
    }

    for rar_set_key, verwacht_rel_pad_norm in (
        bestaande_sleutels - set(kandidaten)
    ):
        database.verbinding.execute(
            """
            DELETE FROM recovery_items
            WHERE rar_set_key = ? AND verwacht_rel_pad_norm = ?
            """,
            (rar_set_key, verwacht_rel_pad_norm)
        )

    database.verbinding.commit()


def toon_recovery_overzicht(overzicht, items, uitvoer=None):
    """
    Toon aantallen en de actuele herstelkandidaten.
    """

    uitvoer = uitvoer or sys.stdout
    uitvoer.write("\nRECOVERY-ITEMS\n")
    uitvoer.write(f"Totaal             : {overzicht['totaal']}\n")
    uitvoer.write(f"Ontbreekt          : {overzicht['ontbreekt']}\n")
    uitvoer.write(f"Corrupt            : {overzicht['corrupt']}\n")
    uitvoer.write(f"Nul bytes          : {overzicht['nul_bytes']}\n")
    uitvoer.write(
        f"Grootteafwijking   : {overzicht['grootte_afwijking']}\n"
    )
    uitvoer.write(f"RAR CRC            : {overzicht['rar_crc']}\n")

    for item in items:
        uitvoer.write(
            f"- [{item['rar_set_key']}] "
            f"{item['verwacht_rel_pad']} "
            f"({item['probleem_type']})\n"
        )
