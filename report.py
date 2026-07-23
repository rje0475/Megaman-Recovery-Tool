from pathlib import Path
from datetime import datetime

from database import verkrijg_ontbrekende_rar_items
from database import verkrijg_rar_inventory_overzicht
from database import verkrijg_recovery_items
from database import verkrijg_recovery_overzicht
from identity import verkrijg_identiteit_overzicht
from spotify_recovery import verkrijg_spotify_recovery_overzicht
from par_inventory import verkrijg_par_overzicht


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
    identiteit_overzicht = verkrijg_identiteit_overzicht(database)
    spotify_recovery = verkrijg_spotify_recovery_overzicht(database)
    par_overzicht = verkrijg_par_overzicht(database)

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

        f.write("PAR2-inventaris\n")
        f.write("------------------------------\n")
        f.write(f"PAR2-sets           : {par_overzicht['par_sets']}\n")
        f.write(
            f"Gekoppelde RAR-sets : "
            f"{par_overzicht['gekoppelde_rar_sets']}\n"
        )
        f.write(f"Repareerbaar        : {par_overzicht['repareerbaar']}\n")
        f.write(
            f"Niet repareerbaar   : "
            f"{par_overzicht['niet_repareerbaar']}\n"
        )
        f.write(f"Geen PAR            : {par_overzicht['geen_par']}\n")
        f.write(f"Geen RAR            : {par_overzicht['geen_rar']}\n")
        f.write(f"Onbekend            : {par_overzicht['onbekend']}\n\n")

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

        f.write("Recovery-identiteit\n")
        f.write("------------------------------\n")
        f.write(
            f"Artiest en titel : "
            f"{identiteit_overzicht['artiest_en_titel']}\n"
        )
        f.write(
            f"Alleen titel     : "
            f"{identiteit_overzicht['alleen_titel']}\n"
        )
        f.write(
            f"Zonder identiteit: "
            f"{identiteit_overzicht['zonder_identiteit']}\n"
        )
        for bron, aantal in identiteit_overzicht["bronnen"].items():
            f.write(f"Bron {bron}: {aantal}\n")
        f.write("\n")

        for item in recovery_items:
            if item["bepaalde_titel"] is None:
                reden = (
                    item["identiteit_reden"]
                    or item["identiteit_bron"]
                    or "niet bepaald"
                )
                f.write(
                    f"Geen identiteit: ID {item['id']} | "
                    f"{item['rar_set_key']} | "
                    f"{item['verwacht_rel_pad']} | "
                    f"{reden}\n"
                )
        f.write("\n")

        f.write("Spotify recovery\n")
        f.write("------------------------------\n")
        f.write(f"Geschikt             : {spotify_recovery['geschikt']}\n")
        f.write(f"Gevonden             : {spotify_recovery['gevonden']}\n")
        f.write(f"Ambiguous            : {spotify_recovery['ambiguous']}\n")
        f.write(
            f"Niet gevonden        : {spotify_recovery['niet_gevonden']}\n"
        )
        f.write(f"Fouten               : {spotify_recovery['fouten']}\n")
        f.write(
            f"Onvoldoende identiteit: "
            f"{spotify_recovery['onvoldoende_identiteit']}\n"
        )
        f.write(
            f"Playlist-tracks      : "
            f"{spotify_recovery['playlist_tracks']}\n\n"
        )

        ambiguous_items = database.verbinding.execute(
            """
            SELECT r.id, r.bepaalde_artiest, r.bepaalde_titel,
                   p.gevonden_artiest, p.gevonden_titel, p.matchscore
            FROM recovery_provider_resultaten p
            JOIN recovery_items r ON r.id = p.recovery_item_id
            WHERE p.provider = 'spotify'
              AND p.resultaat_type = 'ambiguous'
            ORDER BY r.id
            """
        )
        for item in ambiguous_items:
            f.write(
                f"Ambiguous ID {item['id']}: "
                f"{item['bepaalde_artiest']} - "
                f"{item['bepaalde_titel']} => "
                f"{item['gevonden_artiest']} - "
                f"{item['gevonden_titel']} "
                f"({item['matchscore']:.4f})\n"
            )
        f.write("\n")

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
