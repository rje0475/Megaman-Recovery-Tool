import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.external_tools import ToolResultaat, detecteer_winrar


@dataclass(frozen=True)
class HersteldeArchiveSet:
    soort: str
    classificatie: str
    volumes: tuple[Path, ...]
    eerste_volume: Path | None
    verwacht_aantal: int
    ontbrekende_delen: tuple[int, ...]


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
    herstelde_sets: tuple[HersteldeArchiveSet, ...] = ()


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


def _classificeer_genummerde_set(volumes, verwacht_aantal, soort):
    volumes = tuple(sorted(volumes, key=lambda item: item[0]))
    nummers = tuple(nummer for nummer, _ in volumes)
    paden = tuple(pad for _, pad in volumes)
    heeft_eerste = bool(nummers and nummers[0] in (0, 1))
    if not heeft_eerste:
        classificatie = "INVALID"
    elif verwacht_aantal > 1 and len(paden) == 1:
        classificatie = "SINGLE_VOLUME"
    elif len(paden) == verwacht_aantal and (
        nummers == tuple(range(1, verwacht_aantal + 1))
        or nummers == tuple(range(0, verwacht_aantal))
    ):
        classificatie = "COMPLETE"
    else:
        classificatie = "PARTIAL"
    start = 0 if nummers and nummers[0] == 0 else 1
    verwacht = set(range(start, start + verwacht_aantal))
    ontbrekend = tuple(sorted(verwacht - set(nummers)))
    return HersteldeArchiveSet(
        soort, classificatie, paden, paden[0] if heeft_eerste else None,
        verwacht_aantal, ontbrekend,
    )


def classificeer_archive_set(volumes, verwacht_aantal, soort="origineel"):
    genummerd = []
    for pad in volumes:
        pad = Path(pad)
        part = re.search(r"\.part(\d+)\.rar$", pad.name, re.IGNORECASE)
        oud = re.search(r"\.r(\d+)$", pad.name, re.IGNORECASE)
        if part:
            nummer = int(part.group(1))
        elif pad.suffix.casefold() == ".rar":
            nummer = 0
        elif oud:
            nummer = int(oud.group(1)) + 1
        else:
            continue
        genummerd.append((nummer, pad))
    return _classificeer_genummerde_set(
        genummerd, verwacht_aantal, soort
    )


def vind_herstelde_sets(workspace, verwacht_aantal):
    """Vind en classificeer alle generieke rebuilt/repaired volumesets."""
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
        soort = (
            "rebuilt"
            if match.group("markering").casefold().startswith("rebuilt")
            else "repaired"
        )
        part_match = part.match(origineel)
        if part_match:
            sleutel = (
                soort,
                part_match.group("basis").casefold(),
            )
            groepen.setdefault(sleutel, []).append(
                (int(part_match.group("deel")), pad)
            )
        else:
            sleutel = (
                soort, Path(origineel).stem.casefold()
            )
            groepen.setdefault(sleutel, []).append((0, pad))
    resultaten = tuple(
        _classificeer_genummerde_set(volumes, verwacht_aantal, sleutel[0])
        for sleutel, volumes in groepen.items()
    )
    volgorde = {"COMPLETE": 0, "PARTIAL": 1, "SINGLE_VOLUME": 2, "INVALID": 3}
    return tuple(sorted(
        resultaten,
        key=lambda item: (
            volgorde[item.classificatie],
            item.soort,
            item.eerste_volume.name.casefold() if item.eerste_volume else "",
        )
    ))


def vind_herstelde_volumes(workspace, verwacht_aantal=None):
    """Compatibele helper: geef de beste bruikbare herstelde volumeset."""
    verwacht_aantal = verwacht_aantal or 1
    sets = vind_herstelde_sets(workspace, verwacht_aantal)
    bruikbaar = tuple(
        item for item in sets if item.classificatie != "INVALID"
    )
    return bruikbaar[0].volumes if bruikbaar else ()


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
    herstelde_sets = vind_herstelde_sets(workspace, len(volumes))
    bruikbare_sets = tuple(
        item for item in herstelde_sets if item.classificatie != "INVALID"
    )
    hersteld = bruikbare_sets[0].volumes if bruikbare_sets else ()
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
        gemaakt, hersteld, gekozen, fout, commando, herstelde_sets,
    )
