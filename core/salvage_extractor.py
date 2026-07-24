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


def salvage_extract(
    archive, uitvoermap, tool=None, runner=subprocess.run,
):
    archive, uitvoermap = Path(archive).resolve(), Path(uitvoermap).resolve()
    uitvoermap.mkdir(parents=True, exist_ok=True)
    tool = tool or detecteer_7zip()
    if not tool.beschikbaar:
        return ExtractieResultaat(
            "TOOL_NOT_FOUND", archive, uitvoermap, None, "", "", 0, 0, (),
            tool.foutmelding,
        )
    try:
        proces = runner(
            [
                str(tool.pad), "x", str(archive),
                f"-o{uitvoermap}", "-y", "-aos",
            ],
            cwd=str(archive.parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=False,
        )
    except OSError as fout:
        return ExtractieResultaat(
            "FAILED", archive, uitvoermap, None, "", "", 0, 0, (), str(fout)
        )
    bestanden = [p for p in uitvoermap.rglob("*") if p.is_file()]
    mp3s = [p for p in bestanden if p.suffix.casefold() == ".mp3"]
    tekst = f"{proces.stdout or ''}\n{proces.stderr or ''}"
    fouten = tuple(
        regel.strip() for regel in tekst.splitlines()
        if re.search(r"(?i)(crc failed|data error)", regel)
    )
    status = (
        "SUCCESS" if proces.returncode == 0
        else "PARTIAL" if bestanden else "FAILED"
    )
    return ExtractieResultaat(
        status, archive, uitvoermap, proces.returncode,
        proces.stdout or "", proces.stderr or "", len(bestanden), len(mp3s),
        fouten, None if bestanden else "Geen bestanden uitgepakt.",
    )
