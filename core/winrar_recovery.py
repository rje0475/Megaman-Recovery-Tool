import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.external_tools import ToolResultaat, detecteer_winrar


@dataclass(frozen=True)
class WinRarResultaat:
    status: str
    bronset: tuple[Path, ...]
    workspace: Path
    main_archive: Path
    exitcode: int | None
    stdout: str
    stderr: str
    gemaakte_bestanden: tuple[Path, ...]
    herstelde_volumes: tuple[Path, ...]
    gekozen_archive: Path
    foutmelding: str | None = None
    commando: tuple[str, ...] = ()


WINRAR_VOLLEDIG_EXITCODES = frozenset({0})
WINRAR_GEDEELTELIJK_EXITCODES = frozenset({1})


def _is_console_rar(tool):
    return bool(tool.pad and Path(tool.pad).name.casefold() == "rar.exe")


def _recovery_commando(tool, archive):
    switches = ["-inul", "-y"]
    if not _is_console_rar(tool):
        switches.insert(0, "-ibck")
    return tuple([str(tool.pad), "r", *switches, str(archive)])


def _volume_sorteersleutel(pad):
    naam = Path(pad).name
    part = re.search(r"\.part(\d+)\.rar$", naam, re.IGNORECASE)
    if part:
        return (0, int(part.group(1)), naam.casefold())
    oud = re.search(r"\.r(\d+)$", naam, re.IGNORECASE)
    if oud:
        return (1, int(oud.group(1)) + 1, naam.casefold())
    return (1, 0, naam.casefold())


def vind_herstelde_volumes(workspace):
    """Vind een complete, generiek benoemde rebuilt/repaired volumeset."""
    groepen = {}
    patroon = re.compile(
        r"^(?P<markering>rebuilt[._]|repaired[._]?)(?P<origineel>.+)$",
        re.IGNORECASE,
    )
    part = re.compile(
        r"^(?P<basis>.+)\.part(?P<deel>\d+)\.rar$", re.IGNORECASE
    )
    for pad in Path(workspace).iterdir():
        if not pad.is_file() or pad.name.casefold().endswith(".old"):
            continue
        match = patroon.match(pad.name)
        if not match:
            continue
        origineel = match.group("origineel")
        part_match = part.match(origineel)
        if part_match:
            sleutel = (
                match.group("markering").casefold(),
                part_match.group("basis").casefold(),
            )
            groepen.setdefault(sleutel, []).append(
                (int(part_match.group("deel")), pad)
            )
        else:
            sleutel = (
                match.group("markering").casefold(), Path(origineel).stem.casefold()
            )
            groepen.setdefault(sleutel, []).append((0, pad))
    kandidaten = []
    for volumes in groepen.values():
        gesorteerd = tuple(
            pad for _, pad in sorted(
                volumes, key=lambda item: (
                    item[0], item[1].name.casefold()
                )
            )
        )
        eerste_nummer = min(nummer for nummer, _ in volumes)
        if eerste_nummer in (0, 1):
            kandidaten.append(gesorteerd)
    if not kandidaten:
        return ()
    return max(
        kandidaten,
        key=lambda volumes: (len(volumes), volumes[0].name.casefold()),
    )


def voer_winrar_recovery_uit(
    volumes, workspace, tool=None, runner=subprocess.run,
):
    volumes = tuple(Path(p).resolve() for p in volumes)
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    if not volumes:
        raise ValueError("RAR-set bevat geen volumes.")
    main = volumes[0]
    tool = tool or detecteer_winrar()
    kopieen = []
    for volume in volumes:
        doel = workspace / volume.name
        shutil.copy2(volume, doel)
        kopieen.append(doel)
    werk_main = kopieen[0]
    if not tool.beschikbaar:
        return WinRarResultaat(
            "TOOL_NOT_FOUND", volumes, workspace, main, None, "", "",
            tuple(kopieen), (), werk_main, tool.foutmelding, (),
        )
    vooraf = {p.name.casefold() for p in workspace.iterdir()}
    commando = _recovery_commando(tool, werk_main)
    try:
        proces = runner(
            list(commando),
            cwd=str(workspace), capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=False,
        )
        stdout, stderr, exitcode = (
            proces.stdout or "", proces.stderr or "", proces.returncode
        )
    except OSError as fout:
        return WinRarResultaat(
            "FAILED", volumes, workspace, main, None, "", "", tuple(kopieen),
            (), werk_main, str(fout), commando,
        )
    gemaakt = tuple(sorted(
        (
            p for p in workspace.iterdir()
            if p.is_file() and p.name.casefold() not in vooraf
        ),
        key=_volume_sorteersleutel,
    ))
    hersteld = vind_herstelde_volumes(workspace)
    gekozen = hersteld[0] if hersteld else werk_main
    if hersteld and exitcode in WINRAR_VOLLEDIG_EXITCODES:
        status = "SUCCESS"
    elif hersteld and exitcode in WINRAR_GEDEELTELIJK_EXITCODES:
        status = "PARTIAL"
    elif hersteld:
        # Een rebuilt set is bruikbaar bewijs, ook bij een fatale toolcode.
        status = "PARTIAL"
    else:
        status = "FAILED"
    fout = None if hersteld else (
        stderr.strip() or "WinRAR maakte geen herstelde volumes."
    )
    return WinRarResultaat(
        status, volumes, workspace, main, exitcode, stdout, stderr,
        gemaakt, hersteld, gekozen, fout, commando,
    )
