#!/usr/bin/env python3
"""Bouw en controleer een veilige end-to-end recovery-demo."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import (  # noqa: E402
    maak_database,
    verkrijg_rar_inventory_overzicht,
    verkrijg_recovery_items,
)
from identity import bepaal_recovery_identiteiten  # noqa: E402
from rar import test_rar  # noqa: E402
from rar_inventory import (  # noqa: E402
    RarListingResultaat,
    voer_rar_inventory_uit,
)
from recovery import genereer_recovery_items  # noqa: E402
from report import maak_rapport  # noqa: E402
import scanner as scanner_module  # noqa: E402
from scanner import controleer_mp3_bestanden, zoek_mp3_bestanden  # noqa: E402
from spotify import MuziekResultaat  # noqa: E402
from spotify_recovery import voer_spotify_recovery_uit  # noqa: E402


DEMO_MARKER = ".megaman_recovery_demo"
DEMO_MOCK_ENV = "MEGAMAN_DEMO_SPOTIFY"
DEMO_ROOT = PROJECT_ROOT / "demo_runs"
FFMPEG_KANDIDATEN = (
    Path(r"C:\ffmpeg\ffmpeg.exe"),
    Path("ffmpeg"),
)
RAR_KANDIDATEN = (
    Path(r"C:\Program Files\WinRAR\Rar.exe"),
    Path("rar"),
)

DEMO_TRACKS = (
    "01 - Demo Artist - Complete Track.mp3",
    "02 - Demo Artist - Missing Track.mp3",
    "03 - Demo Artist - Empty Track.mp3",
    "04 - Demo Artist - Corrupt Track.mp3",
    "05.mp3",
)


class DemoFout(RuntimeError):
    """Een concrete controle van de praktijktest is mislukt."""


@dataclass(frozen=True)
class DemoPaden:
    root: Path
    origineel: Path
    uitgepakt: Path
    rar: Path
    database: Path
    playlist: Path
    fixture: Path


class DemoSpotifyClient:
    """Vaste lokale Spotify-antwoorden; doet nooit netwerkverkeer."""

    def __init__(self):
        self.aanroepen = []

    def zoek_nummers(self, artiest, titel, limiet=10):
        self.aanroepen.append((artiest, titel))
        if titel == "Missing Track":
            return [self._resultaat(
                artiest, titel, "demo-found", "Demo Album"
            )]
        if titel == "Empty Track":
            return [self._resultaat(
                artiest, "Empty Demo", "demo-ambiguous", "Ander Album"
            )]
        if titel == "Corrupt Track":
            return []
        raise DemoFout(
            f"Onverwachte mock-Spotifyaanroep: {artiest} - {titel}"
        )

    @staticmethod
    def _resultaat(artiest, titel, track_id, album):
        return MuziekResultaat(
            provider="spotify",
            zoek_artiest=artiest,
            zoek_titel=titel,
            gevonden=True,
            track_id=track_id,
            url=f"https://open.spotify.com/track/{track_id}",
            artiest=artiest,
            titel=titel,
            album=album,
            duur_ms=1000,
        )


def maak_demo_spotify_client(demo_mock=False, omgeving=None):
    """Activeer de mock uitsluitend met een expliciete demo-schakelaar."""

    omgeving = omgeving if omgeving is not None else os.environ
    actief = demo_mock or omgeving.get(DEMO_MOCK_ENV) == "1"
    if not actief:
        raise DemoFout(
            f"Demo-Spotify is niet actief. Gebruik --run of zet "
            f"{DEMO_MOCK_ENV}=1."
        )
    return DemoSpotifyClient()


def _vind_programma(kandidaten):
    for kandidaat in kandidaten:
        if kandidaat.is_absolute() and kandidaat.exists():
            return str(kandidaat)
        gevonden = shutil.which(str(kandidaat))
        if gevonden:
            return gevonden
    return None


def maak_demo_paden(basis_map=None, nu=None):
    basis = Path(basis_map) if basis_map else DEMO_ROOT
    tijd = (nu or datetime.now()).strftime("%Y%m%d_%H%M%S")
    root = basis / f"recovery_demo_{tijd}_{uuid.uuid4().hex[:8]}"
    if root.exists():
        raise DemoFout(f"Nieuwe demomap bestaat onverwacht al: {root}")
    root.mkdir(parents=True)
    (root / DEMO_MARKER).write_text(
        str(root.resolve()) + "\n",
        encoding="utf-8",
    )
    paden = DemoPaden(
        root=root,
        origineel=root / "origineel" / "Demo Album",
        uitgepakt=root / "uitgepakt" / "Demo Album",
        rar=root / "rar",
        database=root / "megaman_demo.sqlite3",
        playlist=root / "spotify_recovery_playlist.json",
        fixture=root / "rar_listing_fixture.json",
    )
    paden.origineel.mkdir(parents=True)
    paden.uitgepakt.mkdir(parents=True)
    paden.rar.mkdir(parents=True)
    return paden


def _maak_audio(ffmpeg, doel, frequentie):
    resultaat = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"sine=frequency={frequentie}:duration=1",
            "-q:a", "7", str(doel),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if resultaat.returncode != 0 or not doel.exists():
        raise DemoFout(
            f"FFmpeg kon {doel.name} niet maken: "
            f"{resultaat.stderr.strip()}"
        )


def maak_demo_audio(paden, ffmpeg=None):
    ffmpeg = ffmpeg or _vind_programma(FFMPEG_KANDIDATEN)
    if not ffmpeg:
        raise DemoFout(
            "FFmpeg ontbreekt. Installeer FFmpeg of plaats het op "
            r"C:\ffmpeg\ffmpeg.exe."
        )
    for index, naam in enumerate(DEMO_TRACKS, start=1):
        _maak_audio(ffmpeg, paden.origineel / naam, 300 + index * 80)
    shutil.copytree(
        paden.origineel,
        paden.uitgepakt,
        dirs_exist_ok=True,
    )
    (paden.uitgepakt / DEMO_TRACKS[1]).unlink()
    (paden.uitgepakt / DEMO_TRACKS[2]).write_bytes(b"")
    corrupt = paden.uitgepakt / DEMO_TRACKS[3]
    inhoud = corrupt.read_bytes()
    corrupt.write_bytes(inhoud[:32])
    (paden.uitgepakt / DEMO_TRACKS[4]).unlink()
    return ffmpeg


def _hernoem_rar_volumes(rar_map):
    volumes = sorted(rar_map.glob("demo.part*.rar"))
    for volume in reversed(volumes):
        deel = volume.stem.rsplit("part", 1)[-1]
        volume.rename(rar_map / f"demo.part{int(deel):02d}.rar")
    return sorted(rar_map.glob("demo.part??.rar"))


def maak_rar_of_fixture(paden, rar_programma=None):
    rar_programma = (
        rar_programma
        if rar_programma is not None
        else _vind_programma(RAR_KANDIDATEN)
    )
    if rar_programma:
        resultaat = subprocess.run(
            [
                rar_programma, "a", "-idq", "-ma5", "-v8k",
                str(paden.rar / "demo.rar"),
                r"Demo Album\*.mp3",
            ],
            cwd=paden.origineel.parent,
            capture_output=True,
            text=True,
            timeout=120,
        )
        volumes = _hernoem_rar_volumes(paden.rar)
        if resultaat.returncode == 0 and volumes:
            return "echte multipart RAR-set", None
        raise DemoFout(
            f"RAR-aanmaaktool faalde: {resultaat.stderr.strip()}"
        )

    items = []
    for bestand in sorted(paden.origineel.glob("*.mp3")):
        stat = bestand.stat()
        items.append({
            "verwacht_rel_pad": f"Demo Album\\{bestand.name}",
            "verwacht_rel_pad_norm":
                f"demo album\\{bestand.name.casefold()}",
            "verwachte_map": "Demo Album",
            "verwachte_bestandsnaam": bestand.name,
            "verwachte_grootte": stat.st_size,
            "verwachte_crc32": None,
            "verwachte_modified":
                datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    paden.fixture.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (paden.rar / "demo.part01.rar").write_text(
        "DEMO-FIXTURE: geen echt RAR-volume\n",
        encoding="utf-8",
    )
    return "gedocumenteerde listingfixture (geen RAR-tool)", items


def _fixture_lezer(items):
    def lees(_rar_set):
        return RarListingResultaat(
            items=tuple(items),
            volledig=True,
            fout=None,
        )
    return lees


def _controleer(voorwaarde, melding):
    if not voorwaarde:
        raise DemoFout(melding)


def voer_demo_uit(basis_map=None, uitvoer=None, rar_programma=None):
    uitvoer = uitvoer or sys.stdout
    paden = maak_demo_paden(basis_map)
    database = None
    try:
        ffmpeg = maak_demo_audio(paden)
        variant, fixture_items = maak_rar_of_fixture(
            paden, rar_programma=rar_programma
        )
        uitvoer.write(f"RAR-variant: {variant}\n")
        database = maak_database(paden.database)
        scanner_module.FFMPEG = ffmpeg
        controleer_mp3_bestanden(
            zoek_mp3_bestanden(paden.uitgepakt.parent),
            paden.uitgepakt.parent,
            database,
        )
        listing_lezer = (
            _fixture_lezer(fixture_items) if fixture_items else None
        )
        inventaris = voer_rar_inventory_uit(
            paden.rar,
            database,
            uitvoer=uitvoer,
            listing_lezer=listing_lezer,
        )
        if fixture_items is None:
            for startbestand in paden.rar.glob("*.part01.rar"):
                test_rar(startbestand, database)
        genereer_recovery_items(database, uitvoer=uitvoer)
        bepaal_recovery_identiteiten(database, uitvoer=uitvoer)
        spotify = voer_spotify_recovery_uit(
            database,
            client=maak_demo_spotify_client(demo_mock=True),
            uitvoer=uitvoer,
            export_pad=paden.playlist,
            slaapfunctie=lambda _seconden: None,
        )
        cwd = Path.cwd()
        os.chdir(paden.root)
        try:
            rapport = maak_rapport(paden.uitgepakt.parent, database)
        finally:
            os.chdir(cwd)

        recovery_items = verkrijg_recovery_items(database)
        nul_bytes = sum(
            1 for item in database.values() if item["nul_bytes"]
        )
        corrupt = sum(
            1 for item in database.values()
            if item["ffmpeg"]["status"] == "ERROR"
        )
        playlist = json.loads(paden.playlist.read_text(encoding="utf-8"))

        verwacht = {
            "verwachte_mp3s": 5,
            "aangetroffen_mp3s": 3,
            "ontbrekende_mp3s": 2,
            "nul_bytes": 1,
            "corrupt": 1,
            "recovery_items": 4,
            "spotify_found": 1,
            "spotify_ambiguous": 1,
            "spotify_not_found": 1,
            "playlist_tracks": 1,
            "onvoldoende_identiteit": 1,
        }
        werkelijk = {
            "verwachte_mp3s": inventaris["verwachte_mp3s"],
            "aangetroffen_mp3s": inventaris["aangetroffen_mp3s"],
            "ontbrekende_mp3s": inventaris["ontbrekende_mp3s"],
            "nul_bytes": nul_bytes,
            "corrupt": corrupt,
            "recovery_items": len(recovery_items),
            "spotify_found": spotify.gevonden,
            "spotify_ambiguous": spotify.ambiguous,
            "spotify_not_found": spotify.niet_gevonden,
            "playlist_tracks": len(playlist),
            "onvoldoende_identiteit": spotify.onvoldoende_identiteit,
        }
        for sleutel, waarde in verwacht.items():
            _controleer(
                werkelijk[sleutel] == waarde,
                f"{sleutel}: verwacht {waarde}, gekregen "
                f"{werkelijk[sleutel]}",
            )
        _controleer(
            (paden.root / rapport).exists(),
            f"Rapport ontbreekt: {paden.root / rapport}",
        )

        uitvoer.write("\nDEMO-RESULTAAT\n")
        for sleutel, waarde in werkelijk.items():
            uitvoer.write(f"{sleutel:24}: {waarde}\n")
        uitvoer.write(f"FFmpeg                 : {ffmpeg}\n")
        uitvoer.write(f"Rapport                : {paden.root / rapport}\n")
        uitvoer.write(f"Demomap                : {paden.root.resolve()}\n")
        uitvoer.write("PASS\n")
        return paden.root
    except Exception as fout:
        uitvoer.write(f"\nFAIL: {fout}\n")
        uitvoer.write(f"Demomap: {paden.root.resolve()}\n")
        raise
    finally:
        if database is not None:
            database.sluit()


def ruim_demo_op(pad):
    doel = Path(pad).resolve()
    marker = doel / DEMO_MARKER
    if not doel.is_dir() or not marker.is_file():
        raise DemoFout(
            f"Cleanup geweigerd: marker ontbreekt in {doel}"
        )
    gemarkeerd_pad = marker.read_text(encoding="utf-8").strip()
    if gemarkeerd_pad != str(doel):
        raise DemoFout(
            f"Cleanup geweigerd: marker hoort niet bij {doel}"
        )
    shutil.rmtree(doel)
    return doel


def maak_parser():
    parser = argparse.ArgumentParser(
        description="Maak of verwijder een geïsoleerde recovery-demo."
    )
    actie = parser.add_mutually_exclusive_group(required=True)
    actie.add_argument(
        "--run", action="store_true",
        help="bouw en valideer een nieuwe volledige demo",
    )
    actie.add_argument(
        "--cleanup", metavar="DEMOMAP",
        help="verwijder uitsluitend een gemarkeerde demomap",
    )
    actie.add_argument(
        "--cleanup-all", action="store_true",
        help="verwijder alle gemarkeerde demo's onder demo_runs",
    )
    return parser


def main(argv=None):
    args = maak_parser().parse_args(argv)
    try:
        if args.run:
            voer_demo_uit()
        elif args.cleanup:
            verwijderd = ruim_demo_op(args.cleanup)
            print(f"Verwijderd: {verwijderd}")
        else:
            verwijderd = 0
            if DEMO_ROOT.exists():
                for pad in DEMO_ROOT.iterdir():
                    if pad.is_dir() and (pad / DEMO_MARKER).is_file():
                        ruim_demo_op(pad)
                        verwijderd += 1
            print(f"Verwijderde demomappen: {verwijderd}")
    except Exception:
        if not args.run:
            print("FAIL: cleanup is geweigerd of mislukt.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
