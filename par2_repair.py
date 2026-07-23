"""Expliciete, schrijvende PAR2-reparatieworkflow."""

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from database import DATABASE_BESTAND, SQLiteDatabase
from par2_verifier import (
    MAX_PROCESUITVOER,
    maak_repair_opdracht,
    serialiseer_command,
    vind_par2_executable,
    voer_par2_verificatie_uit,
)
from par_inventory import detecteer_par_sets


class Par2RepairFout(RuntimeError):
    """Een duidelijke, verwachte fout van de repair-opdracht."""


@dataclass(frozen=True)
class RepairOverzicht:
    totaal: int
    gerepareerd: int
    overgeslagen: int
    mislukt: int


def _begrens(tekst):
    tekst = tekst or ""
    if len(tekst) <= MAX_PROCESUITVOER:
        return tekst
    return tekst[:MAX_PROCESUITVOER] + "\n...[afgekapt]"


def _status(database, par_set_key):
    rij = database.verbinding.execute(
        """
        SELECT status
        FROM par_inventory
        WHERE par_set_key = ?
        """,
        (par_set_key,),
    ).fetchone()
    return rij["status"] if rij else "UNKNOWN"


def _bewaar_repair(database, gegevens):
    database.verbinding.execute(
        """
        INSERT INTO par_repair_results (
          par_set_key, par2_file, status_before, started_at, finished_at,
          executable_path, command, exit_code, result, final_status,
          stdout, stderr, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gegevens["par_set_key"], str(gegevens["par2_file"]),
            gegevens["status_before"], gegevens["started_at"],
            gegevens["finished_at"], gegevens.get("executable_path"),
            json.dumps(gegevens.get("command"), ensure_ascii=False)
            if gegevens.get("command") else None,
            gegevens.get("exit_code"), gegevens["result"],
            gegevens.get("final_status"), _begrens(gegevens.get("stdout")),
            _begrens(gegevens.get("stderr")), gegevens.get("last_error"),
        ),
    )
    database.verbinding.commit()


def _update_verificatie(database, par_set_key, resultaat):
    nu = resultaat.verified_at
    database.verbinding.execute(
        """
        UPDATE par_inventory
        SET status = ?,
            recovery_blocks_beschikbaar = ?,
            recovery_blocks_benodigd = ?,
            verificatie_tool = ?,
            verificatie_melding = ?,
            bijgewerkt_op = ?
        WHERE par_set_key = ?
        """,
        (
            resultaat.verification_status,
            resultaat.recovery_blocks_beschikbaar,
            resultaat.recovery_blocks_benodigd,
            resultaat.executable_path,
            resultaat.verification_summary,
            nu,
            par_set_key,
        ),
    )
    database.verbinding.execute(
        """
        INSERT INTO par_verifications (
          par_set_key, executable_path, executable_source, par2_file,
          command, return_code, verification_status, verification_summary,
          stdout, stderr, verified_at, duration_ms, timed_out, error_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (par_set_key) DO UPDATE SET
          executable_path = excluded.executable_path,
          executable_source = excluded.executable_source,
          par2_file = excluded.par2_file,
          command = excluded.command,
          return_code = excluded.return_code,
          verification_status = excluded.verification_status,
          verification_summary = excluded.verification_summary,
          stdout = excluded.stdout,
          stderr = excluded.stderr,
          verified_at = excluded.verified_at,
          duration_ms = excluded.duration_ms,
          timed_out = excluded.timed_out,
          error_type = excluded.error_type
        """,
        (
            par_set_key, resultaat.executable_path,
            resultaat.executable_source, resultaat.par2_file,
            serialiseer_command(resultaat.command), resultaat.return_code,
            resultaat.verification_status,
            resultaat.verification_summary, resultaat.stdout,
            resultaat.stderr, resultaat.verified_at,
            resultaat.duration_ms, resultaat.timed_out,
            resultaat.error_type,
        ),
    )
    database.verbinding.commit()


def voer_par2_reparatie_uit(
    par_map,
    database_pad=DATABASE_BESTAND,
    uitvoer=None,
    executable=None,
    runner=subprocess.run,
    verifier=voer_par2_verificatie_uit,
    nu_functie=datetime.now,
):
    """Repareer uitsluitend eerder als REPAIRABLE geverifieerde PAR2-sets."""

    uitvoer = uitvoer or sys.stdout
    par_map = Path(par_map).resolve()
    if not par_map.is_dir():
        raise Par2RepairFout(f"PAR2-map bestaat niet: {par_map}")
    database_pad = Path(database_pad)
    if not database_pad.is_file():
        raise Par2RepairFout(
            f"Database met PAR2-statussen bestaat niet: "
            f"{database_pad.resolve()}"
        )
    executable = executable or vind_par2_executable()
    if executable is None:
        raise Par2RepairFout(
            "PAR2-tool niet gevonden: stel PAR2_PATH in of installeer "
            "een commandline PAR2-tool."
        )

    par_sets = detecteer_par_sets(par_map)
    database = SQLiteDatabase(database_pad)
    telling = {"gerepareerd": 0, "overgeslagen": 0, "mislukt": 0}
    try:
        for par_set in par_sets:
            status = _status(database, par_set.par_set_key)
            gestart = nu_functie().isoformat(timespec="seconds")
            basis = {
                "par_set_key": par_set.par_set_key,
                "par2_file": par_set.startbestand.resolve(),
                "status_before": status,
                "started_at": gestart,
                "executable_path": str(executable.pad),
            }
            if status != "REPAIRABLE":
                melding = (
                    f"Overgeslagen [{par_set.par_set_key}]: status is "
                    f"{status}; alleen REPAIRABLE wordt gerepareerd."
                )
                uitvoer.write(melding + "\n")
                _bewaar_repair(database, {
                    **basis,
                    "finished_at": nu_functie().isoformat(
                        timespec="seconds"
                    ),
                    "result": "SKIPPED",
                    "final_status": status,
                    "last_error": melding,
                })
                telling["overgeslagen"] += 1
                continue

            command = maak_repair_opdracht(
                executable.pad, par_set.startbestand
            )
            uitvoer.write(
                f"Repair gestart [{par_set.par_set_key}].\n"
            )
            stdout = ""
            stderr = ""
            exit_code = None
            foutmelding = None
            try:
                proces = runner(
                    list(command),
                    cwd=str(par_set.startbestand.parent.resolve()),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )
                stdout = proces.stdout or ""
                stderr = proces.stderr or ""
                exit_code = proces.returncode
            except (OSError, ValueError) as fout:
                foutmelding = str(fout)

            if exit_code != 0:
                foutmelding = foutmelding or (
                    stderr.strip()
                    or f"PAR2-repair gaf exitcode {exit_code}."
                )
                uitvoer.write(
                    f"Repair mislukt [{par_set.par_set_key}]: "
                    f"{foutmelding}\n"
                )
                _bewaar_repair(database, {
                    **basis, "command": command,
                    "finished_at": nu_functie().isoformat(
                        timespec="seconds"
                    ),
                    "exit_code": exit_code, "result": "FAILED",
                    "final_status": status, "stdout": stdout,
                    "stderr": stderr, "last_error": foutmelding,
                })
                telling["mislukt"] += 1
                continue

            uitvoer.write(
                f"Repair voltooid [{par_set.par_set_key}].\n"
            )
            uitvoer.write(
                f"Opnieuw verifiëren [{par_set.par_set_key}]...\n"
            )
            verificatie = verifier(
                executable, par_set.startbestand
            )
            _update_verificatie(
                database, par_set.par_set_key, verificatie
            )
            eindstatus = verificatie.verification_status
            uitvoer.write(
                f"Eindstatus [{par_set.par_set_key}]: {eindstatus}.\n"
            )
            compleet = eindstatus == "COMPLETE"
            resultaat = "SUCCESS" if compleet else "FAILED"
            foutmelding = (
                None if compleet
                else verificatie.verification_summary
            )
            _bewaar_repair(database, {
                **basis, "command": command,
                "finished_at": nu_functie().isoformat(
                    timespec="seconds"
                ),
                "exit_code": exit_code, "result": resultaat,
                "final_status": eindstatus, "stdout": stdout,
                "stderr": stderr, "last_error": foutmelding,
            })
            telling["gerepareerd" if compleet else "mislukt"] += 1
    finally:
        database.sluit()

    return RepairOverzicht(totaal=len(par_sets), **telling)
