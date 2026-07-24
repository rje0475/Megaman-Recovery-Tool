import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.external_tools import detecteer_7zip


@dataclass(frozen=True)
class ExtractieResultaat:
    status: str
    archive: Path
    uitvoermap: Path
    exitcode: int | None
    stdout: str
    stderr: str
    bestanden: int
    mp3_bestanden: int
    data_fouten: tuple[str, ...]
    foutmelding: str | None = None
    commando: tuple[str, ...] = ()


ZEVENZIP_VOLLEDIG_EXITCODES = frozenset({0})
ZEVENZIP_GEDEELTELIJK_EXITCODES = frozenset({1, 2})


def _scan_resultaat(
    archive, uitvoermap, proces, commando, foutmelding=None
):
    bestanden = [p for p in uitvoermap.rglob("*") if p.is_file()]
    mp3s = [p for p in bestanden if p.suffix.casefold() == ".mp3"]
    tekst = f"{proces.stdout or ''}\n{proces.stderr or ''}"
    fouten = tuple(
        regel.strip() for regel in tekst.splitlines()
        if re.search(r"(?i)(crc failed|data error|checksum error)", regel)
    )
    if proces.returncode in ZEVENZIP_VOLLEDIG_EXITCODES:
        status = "SUCCESS"
    elif bestanden and proces.returncode in ZEVENZIP_GEDEELTELIJK_EXITCODES:
        status = "PARTIAL"
    elif bestanden:
        # Ook bij een fatale toolcode blijft aantoonbaar geredde uitvoer nuttig.
        status = "PARTIAL"
    else:
        status = "FAILED"
    return ExtractieResultaat(
        status, archive, uitvoermap, proces.returncode,
        proces.stdout or "", proces.stderr or "", len(bestanden), len(mp3s),
        fouten, foutmelding if not bestanden else None, tuple(commando),
    )


def salvage_extract(
    archive, uitvoermap, tool=None, runner=subprocess.run,
):
    archive, uitvoermap = Path(archive).resolve(), Path(uitvoermap).resolve()
    uitvoermap.mkdir(parents=True, exist_ok=True)
    tool = tool or detecteer_7zip()
    if not tool.beschikbaar:
        return ExtractieResultaat(
            "TOOL_NOT_FOUND", archive, uitvoermap, None, "", "", 0, 0, (),
            tool.foutmelding, (),
        )
    commando = (
        str(tool.pad), "x", str(archive),
        f"-o{uitvoermap}", "-y", "-aos", "-bb0",
    )
    try:
        proces = runner(
            list(commando),
            cwd=str(archive.parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=False,
        )
    except OSError as fout:
        return ExtractieResultaat(
            "FAILED", archive, uitvoermap, None, "", "", 0, 0, (), str(fout),
            commando,
        )
    return _scan_resultaat(
        archive, uitvoermap, proces, commando, "Geen bestanden uitgepakt."
    )


def winrar_salvage_extract(
    archive, uitvoermap, tool, runner=subprocess.run,
):
    """Pak fouttolerant uit zonder prompts en behoud eerdere uitvoer."""
    archive, uitvoermap = Path(archive).resolve(), Path(uitvoermap).resolve()
    uitvoermap.mkdir(parents=True, exist_ok=True)
    if not tool.beschikbaar:
        return ExtractieResultaat(
            "TOOL_NOT_FOUND", archive, uitvoermap, None, "", "", 0, 0, (),
            tool.foutmelding, (),
        )
    switches = ["-inul", "-y", "-o-", "-kb"]
    if Path(tool.pad).name.casefold() != "rar.exe":
        switches.insert(0, "-ibck")
    commando = tuple(
        [
            str(tool.pad), "x", *switches, str(archive),
            str(uitvoermap) + os.sep,
        ]
    )
    try:
        proces = runner(
            list(commando), cwd=str(archive.parent), capture_output=True,
            text=True, encoding="utf-8", errors="replace", shell=False,
        )
    except OSError as fout:
        return ExtractieResultaat(
            "FAILED", archive, uitvoermap, None, "", "", 0, 0, (), str(fout),
            commando,
        )
    return _scan_resultaat(
        archive, uitvoermap, proces, commando, "Geen bestanden uitgepakt."
    )
