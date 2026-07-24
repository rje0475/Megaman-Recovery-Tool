import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from database import DATABASE_BESTAND, SQLiteDatabase
from rar_inventory import groepeer_rar_sets


MAX_PROCESUITVOER = 100_000


class ExtractieFout(RuntimeError):
    """Een duidelijke, verwachte fout van de extractieopdracht."""


@dataclass(frozen=True)
class ExtractieTool:
    pad: Path
    type: str


@dataclass(frozen=True)
class ExtractieOverzicht:
    totaal: int
    uitgepakt: int
    overgeslagen: int
    mislukt: int
    doelmap: Path


def vind_extractie_tool(env=None, which=shutil.which):
    """Zoek bij voorkeur 7-Zip en val daarna terug op UnRAR."""

    env = os.environ if env is None else env
    kandidaten = []
    if env.get("SEVEN_ZIP_PATH"):
        kandidaten.append((env["SEVEN_ZIP_PATH"], "7ZIP"))
    for naam in ("7z", "7zz", "7za"):
        gevonden = which(naam)
        if gevonden:
            kandidaten.append((gevonden, "7ZIP"))
    kandidaten.append((r"C:\Program Files\7-Zip\7z.exe", "7ZIP"))
    if env.get("UNRAR_PATH"):
        kandidaten.append((env["UNRAR_PATH"], "UNRAR"))
    gevonden = which("unrar")
    if gevonden:
        kandidaten.append((gevonden, "UNRAR"))
    kandidaten.append((r"C:\Program Files\WinRAR\UnRAR.exe", "UNRAR"))
    for pad, type_ in kandidaten:
        kandidaat = Path(pad).expanduser()
        if kandidaat.is_file():
            return ExtractieTool(kandidaat.resolve(), type_)
    return None


def _beperk(tekst):
    tekst = tekst or ""
    if len(tekst) <= MAX_PROCESUITVOER:
        return tekst
    return tekst[:MAX_PROCESUITVOER] + "\n[afgekapt]"


def _laatste_status(database, rar_set_key):
    rij = database.verbinding.execute(
        """
        SELECT verificatie.verification_status
        FROM par_inventory AS inventaris
        JOIN par_verifications AS verificatie
          ON verificatie.par_set_key = inventaris.par_set_key
        WHERE inventaris.gekoppelde_rar_set_key = ?
        ORDER BY verificatie.verified_at DESC, verificatie.id DESC
        LIMIT 1
        """,
        (rar_set_key,),
    ).fetchone()
    return rij["verification_status"] if rij else "UNKNOWN"


def _bewaar_resultaat(database, gegevens):
    database.verbinding.execute(
        """
        INSERT INTO extraction_results (
          rar_set_key, rar_startbestand, bronmap, doelmap, par2_status,
          extraction_status, executable_path, executable_type, command,
          return_code, stdout, stderr, message, started_at, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gegevens["rar_set_key"], str(gegevens["rar_startbestand"]),
            str(gegevens["bronmap"]), str(gegevens["doelmap"]),
            gegevens["par2_status"], gegevens["extraction_status"],
            gegevens.get("executable_path"),
            gegevens.get("executable_type"),
            json.dumps(gegevens.get("command"), ensure_ascii=False)
            if gegevens.get("command") else None,
            gegevens.get("return_code"), _beperk(gegevens.get("stdout")),
            _beperk(gegevens.get("stderr")), gegevens["message"],
            gegevens["started_at"], gegevens.get("duration_ms", 0),
        ),
    )
    database.verbinding.commit()


def _command(tool, startbestand, doelmap):
    if tool.type == "7ZIP":
        return [
            str(tool.pad), "x", str(startbestand),
            f"-o{doelmap}", "-y", "-aos",
        ]
    return [
        str(tool.pad), "x", "-o-", str(startbestand),
        str(doelmap) + os.sep,
    ]


def voer_extractie_uit(
    bronmap,
    database_pad=DATABASE_BESTAND,
    doelmap=None,
    uitvoer=None,
    tool=None,
    runner=subprocess.run,
):
    """Pak COMPLETE multipart-RAR-sets uit vanaf hun eerste part-volume."""

    uitvoer = uitvoer or sys.stdout
    bronmap = Path(bronmap).resolve()
    if not bronmap.is_dir():
        raise ExtractieFout(f"Downloadmap bestaat niet: {bronmap}")
    database_pad = Path(database_pad)
    if not database_pad.is_file():
        raise ExtractieFout(
            f"Database met PAR2-verificaties bestaat niet: "
            f"{database_pad.resolve()}"
        )
    doelmap = (
        Path(doelmap).resolve()
        if doelmap is not None
        else bronmap / "extracted"
    )
    if doelmap == bronmap:
        raise ExtractieFout("De doelmap mag niet de bronmap zelf zijn.")

    rar_sets = groepeer_rar_sets(bronmap)
    database = SQLiteDatabase(database_pad)
    telling = {"uitgepakt": 0, "overgeslagen": 0, "mislukt": 0}
    try:
        for rar_set in rar_sets:
            gestart = datetime.now().isoformat(timespec="seconds")
            status = _laatste_status(database, rar_set.rar_set_key)
            basis = {
                "rar_set_key": rar_set.rar_set_key,
                "rar_startbestand": rar_set.startbestand.resolve(),
                "bronmap": bronmap,
                "doelmap": doelmap,
                "par2_status": status,
                "started_at": gestart,
            }
            if status != "COMPLETE":
                melding = (
                    f"Overgeslagen [{rar_set.rar_set_key}]: laatste "
                    f"PAR2-verificatie is {status}; COMPLETE is vereist."
                )
                uitvoer.write(melding + "\n")
                _bewaar_resultaat(database, {
                    **basis, "extraction_status": "SKIPPED",
                    "message": melding,
                })
                telling["overgeslagen"] += 1
                continue

            gekozen_tool = tool or vind_extractie_tool()
            if gekozen_tool is None:
                melding = (
                    f"Mislukt [{rar_set.rar_set_key}]: "
                    "7-Zip of UnRAR is niet gevonden."
                )
                uitvoer.write(melding + "\n")
                _bewaar_resultaat(database, {
                    **basis, "extraction_status": "FAILED",
                    "message": melding,
                })
                telling["mislukt"] += 1
                continue

            doelmap.mkdir(parents=True, exist_ok=True)
            command = _command(
                gekozen_tool, rar_set.startbestand.resolve(), doelmap
            )
            begin = time.monotonic()
            try:
                resultaat = runner(
                    command,
                    cwd=str(bronmap),
                    capture_output=True,
                    text=True,
                    errors="replace",
                    shell=False,
                )
                duur = round((time.monotonic() - begin) * 1000)
                geslaagd = resultaat.returncode == 0
                extractiestatus = "EXTRACTED" if geslaagd else "FAILED"
                melding = (
                    f"Uitgepakt [{rar_set.rar_set_key}] naar {doelmap}"
                    if geslaagd else
                    f"Mislukt [{rar_set.rar_set_key}]: "
                    f"{gekozen_tool.type} gaf exitcode "
                    f"{resultaat.returncode}."
                )
                _bewaar_resultaat(database, {
                    **basis, "extraction_status": extractiestatus,
                    "executable_path": str(gekozen_tool.pad),
                    "executable_type": gekozen_tool.type,
                    "command": command, "return_code": resultaat.returncode,
                    "stdout": resultaat.stdout, "stderr": resultaat.stderr,
                    "message": melding, "duration_ms": duur,
                })
                sleutel = "uitgepakt" if geslaagd else "mislukt"
                telling[sleutel] += 1
                uitvoer.write(melding + "\n")
            except (OSError, ValueError) as fout:
                duur = round((time.monotonic() - begin) * 1000)
                melding = f"Mislukt [{rar_set.rar_set_key}]: {fout}"
                _bewaar_resultaat(database, {
                    **basis, "extraction_status": "FAILED",
                    "executable_path": str(gekozen_tool.pad),
                    "executable_type": gekozen_tool.type,
                    "command": command, "message": melding,
                    "duration_ms": duur,
                })
                telling["mislukt"] += 1
                uitvoer.write(melding + "\n")
    finally:
        database.sluit()

    return ExtractieOverzicht(
        totaal=len(rar_sets), doelmap=doelmap, **telling
    )
