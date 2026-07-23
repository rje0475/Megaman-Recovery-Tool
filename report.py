from pathlib import Path
from datetime import datetime

from database import verkrijg_ontbrekende_rar_items
from database import verkrijg_rar_inventory_overzicht
from database import verkrijg_recovery_items
from database import verkrijg_recovery_overzicht


def maak_rapport(map_pad, database):

    reports_map = Path("reports")
    reports_map.mkdir(exist_ok=True)

    tijd = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    rapport = reports_map / f"rapport_{tijd}.txt"

    totaal = len(database)

    goed = []
    nul_bytes = []
    rar_fouten = []
    ffmpeg_fouten = []
    rar_overzicht = verkrijg_rar_inventory_overzicht(database)
    ontbrekende_rar_items = verkrijg_ontbrekende_rar_items(database)
    recovery_overzicht = verkrijg_recovery_overzicht(database)
    recovery_items = verkrijg_recovery_items(database)

    for gegevens in database.values():

        if (
            not gegevens["nul_bytes"]
            and gegevens["rar"]["status"] != "ERROR"
            and gegevens["ffmpeg"]["status"] != "ERROR"
        ):
            goed.append(gegevens["bestand"])

        if gegevens["nul_bytes"]:
            nul_bytes.append(gegevens["bestand"])

        if gegevens["rar"]["status"] == "ERROR":
            rar_fouten.append(gegevens)

        if gegevens["ffmpeg"]["status"] == "ERROR":
            ffmpeg_fouten.append(gegevens)

    with open(rapport, "w", encoding="utf-8") as f:

        f.write("=====================================\n")
        f.write("Megaman Recovery Tool\n")
        f.write("=====================================\n\n")

        f.write(f"Map: {map_pad}\n\n")

        f.write(f"Totaal MP3's : {totaal}\n")
        f.write(f"Bestanden OK : {len(goed)}\n")
        f.write(f"0-byte       : {len(nul_bytes)}\n")
        f.write(f"RAR fouten   : {len(rar_fouten)}\n")
        f.write(f"FFmpeg fouten: {len(ffmpeg_fouten)}\n\n")

        f.write("RAR-inventaris\n")
        f.write("------------------------------\n")
        f.write(f"RAR-sets                 : {rar_overzicht['rar_sets']}\n")
        f.write(
            f"Verwachte MP3's          : "
            f"{rar_overzicht['verwachte_mp3s']}\n"
        )
        f.write(
            f"Aangetroffen MP3's       : "
            f"{rar_overzicht['aangetroffen_mp3s']}\n"
        )
        f.write(
            f"Ontbrekende MP3's        : "
            f"{rar_overzicht['ontbrekende_mp3s']}\n"
        )
        f.write(
            f"Afwijkende grootte       : "
            f"{rar_overzicht['grootte_afwijkend']}\n"
        )
        f.write(
            f"Onvolledige RAR-listings : "
            f"{rar_overzicht['listing_fouten']}\n\n"
        )

        if ontbrekende_rar_items:

            f.write("Ontbrekende MP3's uit RAR-inventaris\n")
            f.write("------------------------------\n")

            for item in ontbrekende_rar_items:
                grootte = (
                    item["verwachte_grootte"]
                    if item["verwachte_grootte"] is not None
                    else "onbekend"
                )
                crc = item["verwachte_crc32"] or "onbekend"
                f.write(f"RAR-set : {item['rar_set_key']}\n")
                f.write(f"Pad     : {item['verwacht_rel_pad']}\n")
                f.write(f"Grootte : {grootte}\n")
                f.write(f"CRC     : {crc}\n\n")

        f.write("Recovery-items\n")
        f.write("------------------------------\n")
        f.write(f"Totaal             : {recovery_overzicht['totaal']}\n")
        f.write(
            f"Ontbreekt          : {recovery_overzicht['ontbreekt']}\n"
        )
        f.write(f"Corrupt            : {recovery_overzicht['corrupt']}\n")
        f.write(
            f"Nul bytes          : {recovery_overzicht['nul_bytes']}\n"
        )
        f.write(
            f"Grootteafwijking   : "
            f"{recovery_overzicht['grootte_afwijking']}\n"
        )
        f.write(f"RAR CRC            : {recovery_overzicht['rar_crc']}\n\n")

        for item in recovery_items:
            f.write(f"RAR-set      : {item['rar_set_key']}\n")
            f.write(f"Relatief pad : {item['verwacht_rel_pad']}\n")
            f.write(f"Probleemtype : {item['probleem_type']}\n\n")

        if nul_bytes:

            f.write("0-byte bestanden\n")
            f.write("------------------------------\n")

            for bestand in nul_bytes:
                f.write(f"{bestand.relative_to(map_pad)}\n")

            f.write("\n")

        if rar_fouten:

            f.write("RAR fouten\n")
            f.write("------------------------------\n")

            for gegevens in rar_fouten:
                f.write(f"[{gegevens['rar']['type']}] ")
                f.write(f"{gegevens['relatief_pad']}\n")

            f.write("\n")

        if ffmpeg_fouten:

            f.write("FFmpeg fouten\n")
            f.write("------------------------------\n")

            for gegevens in ffmpeg_fouten:

                f.write(f"[{gegevens['ffmpeg']['type']}] ")
                f.write(f"{gegevens['relatief_pad']}\n")

            f.write("\n")

    return rapport
