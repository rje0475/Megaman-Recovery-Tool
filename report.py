from pathlib import Path
from datetime import datetime


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