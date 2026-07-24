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
            tuple(kopieen), (), werk_main, tool.foutmelding,
        )
    vooraf = {p.name.casefold() for p in workspace.iterdir()}
    try:
        proces = runner(
            [str(tool.pad), "r", str(werk_main)],
            cwd=str(workspace), capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=False,
        )
        stdout, stderr, exitcode = (
            proces.stdout or "", proces.stderr or "", proces.returncode
        )
    except OSError as fout:
        return WinRarResultaat(
            "FAILED", volumes, workspace, main, None, "", "", tuple(kopieen),
            (), werk_main, str(fout),
        )
    gemaakt = tuple(
        sorted(
            (p for p in workspace.iterdir()
             if p.is_file() and p.name.casefold() not in vooraf),
            key=lambda p: p.name.casefold(),
        )
    )
    hersteld = tuple(
        p for p in gemaakt
        if p.name.casefold().startswith(("rebuilt.", "repaired"))
        or "rebuilt" in p.name.casefold()
    )
    gekozen = hersteld[0] if hersteld else werk_main
    if hersteld and exitcode == 0:
        status = "SUCCESS"
    elif hersteld:
        status = "PARTIAL"
    else:
        status = "FAILED"
    fout = None if hersteld else (
        stderr.strip() or "WinRAR maakte geen herstelde volumes."
    )
    return WinRarResultaat(
        status, volumes, workspace, main, exitcode, stdout, stderr,
        gemaakt, hersteld, gekozen, fout,
    )
