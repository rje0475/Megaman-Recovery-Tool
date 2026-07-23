"""Detecteer en voer een externe PAR2-verifier strikt read-only uit."""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


MAX_PROCESUITVOER = 20000
PAR2_TIMEOUT_SECONDEN = 120
PATH_NAMEN = ("par2.exe", "par2", "par2j64.exe", "par2j.exe")
VASTE_WINDOWS_PADEN = (
    r"C:\Program Files\SABnzbd\win\par2\par2.exe",
    r"C:\Program Files\SABnzbd\win\par2\arm64\par2.exe",
    r"C:\Program Files\MultiPar\par2j64.exe",
    r"C:\Program Files\MultiPar\par2j.exe",
    r"C:\Program Files (x86)\MultiPar\par2j64.exe",
    r"C:\Program Files (x86)\MultiPar\par2j.exe",
    r"C:\Program Files\QuickPar\par2.exe",
    r"C:\Program Files (x86)\QuickPar\par2.exe",
)


@dataclass(frozen=True)
class Par2Executable:
    pad: Path
    bron: str


@dataclass(frozen=True)
class Par2Classificatie:
    status: str
    samenvatting: str
    recovery_blocks_beschikbaar: int | None = None
    recovery_blocks_benodigd: int | None = None


@dataclass(frozen=True)
class Par2VerificatieResultaat:
    executable_path: str | None
    executable_source: str | None
    par2_file: str
    command: tuple[str, ...]
    return_code: int | None
    verification_status: str
    verification_summary: str
    stdout: str
    stderr: str
    verified_at: str
    duration_ms: int
    timed_out: bool
    error_type: str | None
    recovery_blocks_beschikbaar: int | None = None
    recovery_blocks_benodigd: int | None = None


def vind_par2_executable(
    omgeving=None,
    which=shutil.which,
    vaste_paden=VASTE_WINDOWS_PADEN,
):
    """Zoek ENV, PATH en vaste Windows-paden in vaste volgorde."""

    omgeving = os.environ if omgeving is None else omgeving
    env_pad = omgeving.get("PAR2_PATH")
    if env_pad:
        kandidaat = Path(env_pad.strip().strip('"'))
        if _bruikbaar(kandidaat):
            return Par2Executable(kandidaat.resolve(), "ENV")

    for naam in PATH_NAMEN:
        gevonden = which(naam)
        if gevonden:
            kandidaat = Path(gevonden)
            if _bruikbaar(kandidaat):
                return Par2Executable(kandidaat.resolve(), "PATH")

    for vast_pad in vaste_paden:
        kandidaat = Path(vast_pad)
        if _bruikbaar(kandidaat):
            return Par2Executable(kandidaat.resolve(), "FIXED_PATH")
    return None


def _bruikbaar(pad):
    return (
        pad.name.casefold() != "quickpar.exe"
        and pad.exists()
        and pad.is_file()
    )


def maak_verify_opdracht(executable, par2_file):
    """Gebruik alleen het verify-subcommand; nooit repair."""

    executable = Path(executable)
    par2_file = Path(par2_file).resolve()
    # par2cmdline ondersteunt het expliciete `verify`; MultiPar par2j
    # gebruikt de equivalente korte opdracht `v`.
    subcommand = (
        "v" if executable.name.casefold() in ("par2j.exe", "par2j64.exe")
        else "verify"
    )
    return (str(executable), subcommand, str(par2_file))


def classificeer_par2_resultaat(stdout, stderr, return_code):
    """Classificeer volledige stdout+stderr met negatieve patronen eerst."""

    tekst = f"{stdout or ''}\n{stderr or ''}"
    laag = tekst.casefold()
    beschikbaar = _zoek_getal(
        tekst,
        (
            r"(?i)(\d+)\s+recovery blocks?\s+(?:are\s+)?available",
            r"(?i)recovery blocks?\s+available\s*[:=]\s*(\d+)",
        ),
    )
    benodigd = _zoek_getal(
        tekst,
        (
            r"(?i)(?:you\s+)?need\s+(\d+)\s+(?:more\s+)?"
            r"recovery blocks?",
            r"(?i)recovery blocks?\s+(?:needed|required)\s*[:=]\s*(\d+)",
        ),
    )
    niet_mogelijk = (
        "repair is not possible",
        "not enough recovery blocks",
        "insufficient recovery blocks",
        "recovery data is insufficient",
        "unable to repair",
        "cannot be repaired",
    )
    compleet = (
        "all files are correct",
        "all files are complete",
        "repair is not required",
        "no repair is required",
        "all files are intact",
    )
    repareerbaar = (
        "repair is possible",
        "repair is required",
        "damaged files can be repaired",
        "missing files can be repaired",
        "you have enough recovery blocks",
    )

    if any(patroon in laag for patroon in niet_mogelijk) or (
        beschikbaar is not None
        and benodigd is not None
        and beschikbaar < benodigd
    ):
        tekort = (
            benodigd - beschikbaar
            if beschikbaar is not None and benodigd is not None
            else None
        )
        samenvatting = (
            f"Reparatie niet mogelijk: {tekort} recovery blocks tekort."
            if tekort is not None
            else "Reparatie niet mogelijk: onvoldoende recovery data."
        )
        status = "NOT_REPAIRABLE"
    elif any(patroon in laag for patroon in compleet):
        status = "COMPLETE"
        samenvatting = "Alle bestanden zijn correct."
    elif any(patroon in laag for patroon in repareerbaar) or (
        beschikbaar is not None
        and benodigd is not None
        and benodigd > 0
        and beschikbaar >= benodigd
    ):
        status = "REPAIRABLE"
        samenvatting = (
            f"Reparatie mogelijk: {beschikbaar} recovery blocks beschikbaar."
            if beschikbaar is not None
            else "Reparatie mogelijk: voldoende recovery blocks."
        )
    else:
        status = "UNKNOWN"
        samenvatting = (
            f"PAR2-uitvoer niet herkend (returncode {return_code})."
        )
    return Par2Classificatie(
        status,
        samenvatting,
        beschikbaar,
        benodigd,
    )


def voer_par2_verificatie_uit(
    executable,
    par2_file,
    runner=subprocess.run,
    timeout=PAR2_TIMEOUT_SECONDEN,
    klok=time.perf_counter,
    nu_functie=datetime.now,
):
    """Start één begrensde verify-aanroep en vang alle procesfouten af."""

    par2_file = Path(par2_file).resolve()
    executable_info = (
        executable
        if isinstance(executable, Par2Executable)
        else Par2Executable(Path(executable), "EXPLICIT")
    )
    command = maak_verify_opdracht(executable_info.pad, par2_file)
    gestart = klok()
    verified_at = nu_functie().isoformat(timespec="seconds")
    stdout = ""
    stderr = ""
    return_code = None
    timed_out = False
    error_type = None

    try:
        resultaat = runner(
            list(command),
            cwd=str(par2_file.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        stdout = resultaat.stdout or ""
        stderr = resultaat.stderr or ""
        return_code = resultaat.returncode
        classificatie = classificeer_par2_resultaat(
            stdout, stderr, return_code
        )
    except subprocess.TimeoutExpired as fout:
        stdout = _naar_tekst(fout.stdout)
        stderr = _naar_tekst(fout.stderr)
        timed_out = True
        error_type = "TIMEOUT"
        classificatie = Par2Classificatie(
            "UNKNOWN", "PAR2-verificatie time-out."
        )
    except FileNotFoundError:
        error_type = "FILE_NOT_FOUND"
        classificatie = Par2Classificatie(
            "UNKNOWN", "PAR2-proces kon niet worden gestart: bestand ontbreekt."
        )
    except PermissionError:
        error_type = "PERMISSION_ERROR"
        classificatie = Par2Classificatie(
            "UNKNOWN", "PAR2-proces kon niet worden gestart: geen toegang."
        )
    except OSError as fout:
        error_type = "OS_ERROR"
        classificatie = Par2Classificatie(
            "UNKNOWN", f"PAR2-proces kon niet worden gestart: {fout}."
        )
    except Exception as fout:
        error_type = type(fout).__name__
        classificatie = Par2Classificatie(
            "UNKNOWN", f"Onverwachte PAR2-procesfout: {type(fout).__name__}."
        )

    duur_ms = max(0, round((klok() - gestart) * 1000))
    return Par2VerificatieResultaat(
        executable_path=str(executable_info.pad),
        executable_source=executable_info.bron,
        par2_file=str(par2_file),
        command=command,
        return_code=return_code,
        verification_status=classificatie.status,
        verification_summary=classificatie.samenvatting,
        stdout=_begrens(stdout),
        stderr=_begrens(stderr),
        verified_at=verified_at,
        duration_ms=duur_ms,
        timed_out=timed_out,
        error_type=error_type,
        recovery_blocks_beschikbaar=
            classificatie.recovery_blocks_beschikbaar,
        recovery_blocks_benodigd=classificatie.recovery_blocks_benodigd,
    )


def onbekend_zonder_tool(par2_file):
    """Maak een expliciet UNKNOWN-resultaat wanneer detectie niets vindt."""

    return Par2VerificatieResultaat(
        executable_path=None,
        executable_source=None,
        par2_file=str(Path(par2_file).resolve()),
        command=(),
        return_code=None,
        verification_status="UNKNOWN",
        verification_summary="PAR2-tool niet gevonden.",
        stdout="",
        stderr="",
        verified_at=datetime.now().isoformat(timespec="seconds"),
        duration_ms=0,
        timed_out=False,
        error_type="TOOL_NOT_FOUND",
    )


def serialiseer_command(command):
    return json.dumps(list(command), ensure_ascii=False)


def _zoek_getal(tekst, patronen):
    for patroon in patronen:
        match = re.search(patroon, tekst)
        if match:
            return int(match.group(1))
    return None


def _begrens(tekst):
    tekst = tekst or ""
    if len(tekst) <= MAX_PROCESUITVOER:
        return tekst
    return tekst[:MAX_PROCESUITVOER] + "\n...[afgekapt]"


def _naar_tekst(waarde):
    if waarde is None:
        return ""
    if isinstance(waarde, bytes):
        return waarde.decode("utf-8", errors="replace")
    return str(waarde)
