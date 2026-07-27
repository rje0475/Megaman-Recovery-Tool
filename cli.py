import argparse
import io
import os
import sys
from pathlib import Path

from analyse import AnalyseFout, voer_analyse
from database import DATABASE_BESTAND
from par2_repair import Par2RepairFout
from rar_extractor import ExtractieFout
from spotify_smart import SpotifyZoekFout
from core.salvage_workflow import SalvageFout
from core.version import VersionInfo


BANNER = (
    "===================================\n"
    "     Megaman Recovery Tool v1.0\n"
    "==================================="
)


def _safe_write(stream, text):
    """Schrijf best-effort; foutafhandeling mag nooit zelf crashen."""
    if stream is None:
        return False
    try:
        stream.write(str(text))
        try:
            stream.flush()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _resolve_output(uitvoer=None):
    if uitvoer is not None:
        return uitvoer
    if sys.stdout is not None:
        return sys.stdout
    try:
        return open(os.devnull, "w", encoding="utf-8")
    except Exception:
        return io.StringIO()


class SafeArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, output_stream=None, **kwargs):
        self.output_stream = output_stream
        super().__init__(*args, **kwargs)

    def _print_message(self, message, file=None):
        _safe_write(file if file is not None else self.output_stream, message)


def maak_parser(uitvoer=None):
    parser = SafeArgumentParser(
        output_stream=_resolve_output(uitvoer),
        prog="python main.py",
        allow_abbrev=False,
        description=(
            "Analyseer MP3-, RAR- en PAR2-bestanden zonder bestanden te "
            "repareren, uit te pakken, te verplaatsen of te verwijderen."
        ),
        epilog=(
            "Voorbeelden:\n"
            "  python main.py\n"
            "  python main.py --gui\n"
            "  python main.py --analyze \"C:\\pad\\naar\\map\"\n"
            "  python main.py --repair \"C:\\pad\\naar\\map\"\n"
            "  python main.py --spotify-search \"C:\\pad\\naar\\map\"\n"
            "  python main.py --spotify-retry \"C:\\pad\\naar\\map\"\n"
            "  python main.py --salvage-rar \"C:\\pad\\naar\\map\"\n"
            "  python main.py --extract \"C:\\pad\\naar\\downloadmap\"\n"
            "  python main.py --demo\n"
            "  python main.py --report"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    acties = parser.add_mutually_exclusive_group()
    parser.add_argument(
        "--version", action="version", version=VersionInfo.current().display
    )
    acties.add_argument(
        "--salvage-rar",
        metavar="MAP",
        help="herstel en pak RAR-sets fouttolerant uit",
    )
    parser.add_argument("--workspace", metavar="MAP")
    parser.add_argument("--rar-set", metavar="NAAM")
    parser.add_argument("--skip-par2", action="store_true")
    parser.add_argument("--skip-winrar", action="store_true")
    parser.add_argument("--no-spotify", action="store_true")
    acties.add_argument(
        "--spotify-search",
        metavar="MAP",
        help="zoek en beoordeel Spotify-kandidaten voor open recovery-items",
    )
    acties.add_argument(
        "--spotify-retry",
        metavar="MAP",
        help="zoek uitsluitend NOT_FOUND- en AMBIGUOUS-items opnieuw",
    )
    acties.add_argument(
        "--gui",
        action="store_true",
        help="start de desktopinterface",
    )
    acties.add_argument(
        "--repair",
        metavar="MAP",
        help=(
            "repareer uitsluitend PAR2-datasets met de opgeslagen status "
            "REPAIRABLE en verifieer ze daarna opnieuw"
        ),
    )
    acties.add_argument(
        "--extract",
        metavar="DOWNLOADMAP",
        help=(
            "pak RAR-sets met een laatste PAR2-status COMPLETE uit naar "
            "de submap 'extracted'"
        ),
    )
    acties.add_argument(
        "--analyze",
        metavar="MAP",
        help=(
            "analyseer deze map recursief als MP3-, RAR- en PAR2-zoekbasis"
        ),
    )
    acties.add_argument(
        "--demo",
        action="store_true",
        help="bouw en controleer de geïsoleerde recovery-demo",
    )
    acties.add_argument(
        "--report",
        action="store_true",
        help="toon het meest recente rapport van de normale database",
    )
    return parser


def toon_laatste_rapport(
    database_pad=DATABASE_BESTAND,
    reports_map=Path("reports"),
    uitvoer=None,
):
    uitvoer = _resolve_output(uitvoer)
    database_pad = Path(database_pad)
    reports_map = Path(reports_map)
    if not database_pad.is_file():
        _safe_write(uitvoer,
            f"Geen normale database gevonden: {database_pad.resolve()}\n"
        )
        return 1
    rapporten = sorted(
        reports_map.glob("rapport_*.txt"),
        key=lambda pad: pad.stat().st_mtime,
        reverse=True,
    ) if reports_map.is_dir() else []
    if not rapporten:
        _safe_write(uitvoer,
            f"Geen rapport gevonden in: {reports_map.resolve()}\n"
        )
        return 1
    rapport = rapporten[0]
    _safe_write(uitvoer, f"Meest recente rapport: {rapport.resolve()}\n\n")
    _safe_write(uitvoer, rapport.read_text(encoding="utf-8"))
    return 0


def _interactieve_paden(invoer):
    mp3_pad = invoer(
        "\nSleep de map met de UITGEPAKTE MP3's hierheen:\n"
    ).strip('"')
    rar_pad = invoer(
        "\nSleep nu de map met de ORIGINELE RAR's hierheen:\n"
    ).strip('"')
    return Path(mp3_pad), Path(rar_pad)


def main(argv=None, invoer=input, uitvoer=None):
    if (
        argv is None
        and getattr(sys, "frozen", False)
        and sys.stdout is None
        and len(sys.argv) == 1
    ):
        argv = ["--gui"]
    uitvoer = _resolve_output(uitvoer)
    parser = maak_parser(uitvoer)
    args = parser.parse_args(argv)
    if (
        args.workspace or args.rar_set or args.skip_par2
        or args.skip_winrar or args.no_spotify
    ) and not args.salvage_rar:
        parser.error(
            "salvage-opties horen bij --salvage-rar"
        )
    try:
        if args.gui:
            from gui import GuiDependencyFout, start_gui
            try:
                return start_gui()
            except GuiDependencyFout as fout:
                _safe_write(uitvoer, f"FOUT: {fout}\n")
                return 1
        _safe_write(uitvoer, BANNER + "\n")
        if args.demo:
            from tools.create_demo_recovery_test import voer_demo_uit
            voer_demo_uit(uitvoer=uitvoer)
            return 0
        if args.report:
            return toon_laatste_rapport(uitvoer=uitvoer)
        if args.salvage_rar:
            from core.salvage_workflow import voer_salvage_workflow_uit
            resultaten = voer_salvage_workflow_uit(
                Path(args.salvage_rar.strip('"')),
                workspace=(
                    Path(args.workspace.strip('"')) if args.workspace else None
                ),
                rar_set=args.rar_set,
                skip_par2=args.skip_par2,
                skip_winrar=args.skip_winrar,
                no_spotify=args.no_spotify,
                uitvoer=uitvoer,
            )
            statussen = {resultaat.eindstatus for resultaat in resultaten}
            return (
                2 if "FAILED" in statussen
                else 1 if "PARTIAL" in statussen
                else 0
            )
        if args.spotify_search or args.spotify_retry:
            from spotify_smart import voer_spotify_smart_uit
            overzicht = voer_spotify_smart_uit(
                Path((args.spotify_search or args.spotify_retry).strip('"')),
                retry=bool(args.spotify_retry),
                uitvoer=uitvoer,
            )
            return 1 if overzicht.fouten else 0
        if args.repair:
            from par2_repair import voer_par2_reparatie_uit
            overzicht = voer_par2_reparatie_uit(
                Path(args.repair.strip('"')), uitvoer=uitvoer
            )
            return 1 if overzicht.mislukt else 0
        if args.extract:
            from rar_extractor import voer_extractie_uit
            overzicht = voer_extractie_uit(
                Path(args.extract.strip('"')), uitvoer=uitvoer
            )
            return 1 if overzicht.mislukt else 0
        if args.analyze:
            map_pad = Path(args.analyze.strip('"'))
            voer_analyse(map_pad, map_pad, uitvoer=uitvoer)
            return 0

        mp3_map, rar_map = _interactieve_paden(invoer)
        voer_analyse(mp3_map, rar_map, uitvoer=uitvoer)
        invoer("\nDruk op Enter om af te sluiten...")
        return 0
    except (
        AnalyseFout, ExtractieFout, Par2RepairFout, SpotifyZoekFout,
        SalvageFout, OSError, ValueError
    ) as fout:
        _safe_write(uitvoer, f"FOUT: {fout}\n")
        return 1
    except KeyboardInterrupt:
        _safe_write(uitvoer, "\nAfgebroken door gebruiker.\n")
        return 130
    except Exception as fout:
        _safe_write(uitvoer,
            f"FOUT: de analyse kon niet worden voltooid: {fout}\n"
        )
        return 1
