import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from paden import normaliseer_relatief_pad_sleutel
from par2_verifier import (
    classificeer_par2_resultaat,
    onbekend_zonder_tool,
    serialiseer_command,
    vind_par2_executable,
    voer_par2_verificatie_uit,
)


PAR_VOLUME = re.compile(
    r"^(?P<basis>.+)\.vol[^.]+\.par2$",
    re.IGNORECASE,
)
PAR_BASIS = re.compile(r"^(?P<basis>.+)\.par2$", re.IGNORECASE)
GELDIGE_STATUSSEN = {
    "OK", "COMPLETE", "REPAIRABLE", "NOT_REPAIRABLE",
    "NO_RAR", "NO_PAR", "UNKNOWN",
}


@dataclass(frozen=True)
class ParSet:
    par_set_key: str
    startbestand: Path
    bestanden: tuple[Path, ...]
    recovery_volumes: tuple[Path, ...]


@dataclass(frozen=True)
class ParVerificatie:
    status: str
    recovery_blocks_beschikbaar: int | None = None
    recovery_blocks_benodigd: int | None = None
    tool: str | None = None
    melding: str | None = None
    tool_source: str | None = None
    par2_file: str | None = None
    command: tuple[str, ...] = ()
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    verified_at: str | None = None
    duration_ms: int = 0
    timed_out: bool = False
    error_type: str | None = None


def detecteer_par_sets(par_map):
    """Groepeer basis- en volume-PAR2-bestanden per relatieve setnaam."""

    par_map = Path(par_map)
    groepen = {}
    for bestand in par_map.rglob("*"):
        if not bestand.is_file():
            continue
        volume = PAR_VOLUME.match(bestand.name)
        basis_match = PAR_BASIS.match(bestand.name)
        if volume:
            basisnaam = volume.group("basis")
            is_volume = True
        elif basis_match:
            basisnaam = basis_match.group("basis")
            is_volume = False
        else:
            continue
        basisnaam = re.sub(
            r"(?i)(?:\.part0*1)?\.rar$",
            "",
            basisnaam,
        )
        relatief_basis = bestand.parent.relative_to(par_map) / basisnaam
        sleutel = normaliseer_relatief_pad_sleutel(relatief_basis)
        groep = groepen.setdefault(
            sleutel, {"basis": [], "volumes": [], "alle": []}
        )
        groep["alle"].append(bestand)
        groep["volumes" if is_volume else "basis"].append(bestand)

    resultaat = []
    for sleutel, groep in groepen.items():
        basisbestanden = sorted(
            groep["basis"], key=lambda pad: str(pad).casefold()
        )
        alle = tuple(sorted(
            groep["alle"], key=lambda pad: str(pad).casefold()
        ))
        resultaat.append(ParSet(
            par_set_key=sleutel,
            startbestand=basisbestanden[0] if basisbestanden else alle[0],
            bestanden=alle,
            recovery_volumes=tuple(sorted(
                groep["volumes"], key=lambda pad: str(pad).casefold()
            )),
        ))
    return sorted(resultaat, key=lambda par_set: par_set.par_set_key)


def koppel_par_aan_rar(par_set_key, rar_set_keys):
    """Koppel uitsluitend een eenduidige setnaam in dezelfde relatieve map."""

    exacte = [
        sleutel for sleutel in rar_set_keys
        if sleutel.casefold() == par_set_key.casefold()
    ]
    return exacte[0] if len(exacte) == 1 else None


def vind_par_tool():
    gevonden = vind_par2_executable()
    return str(gevonden.pad) if gevonden else None


def parseer_par_verificatie(uitvoer, tool=None, returncode=0):
    """Parseer gangbare par2cmdline- en MultiPar-verificatiemeldingen."""

    classificatie = classificeer_par2_resultaat(
        uitvoer, "", returncode
    )
    return ParVerificatie(
        status=classificatie.status,
        recovery_blocks_beschikbaar=
            classificatie.recovery_blocks_beschikbaar,
        recovery_blocks_benodigd=classificatie.recovery_blocks_benodigd,
        tool=tool,
        melding=classificatie.samenvatting,
        return_code=returncode,
        stdout=uitvoer or "",
        verified_at=datetime.now().isoformat(timespec="seconds"),
    )


def verifieer_par_set(par_set, executable=None):
    """Voer uitsluitend PAR2-verificatie uit; nooit reparatie."""

    executable = executable or vind_par2_executable()
    resultaat = (
        voer_par2_verificatie_uit(executable, par_set.startbestand)
        if executable
        else onbekend_zonder_tool(par_set.startbestand)
    )
    return ParVerificatie(
        status=resultaat.verification_status,
        recovery_blocks_beschikbaar=
            resultaat.recovery_blocks_beschikbaar,
        recovery_blocks_benodigd=resultaat.recovery_blocks_benodigd,
        tool=resultaat.executable_path,
        melding=resultaat.verification_summary,
        tool_source=resultaat.executable_source,
        par2_file=resultaat.par2_file,
        command=resultaat.command,
        return_code=resultaat.return_code,
        stdout=resultaat.stdout,
        stderr=resultaat.stderr,
        verified_at=resultaat.verified_at,
        duration_ms=resultaat.duration_ms,
        timed_out=resultaat.timed_out,
        error_type=resultaat.error_type,
    )


def voer_par_inventory_uit(
    par_map, database, uitvoer=None, verificatie_lezer=None
):
    """Synchroniseer de actuele read-only PAR2-inventaris."""

    uitvoer = uitvoer or sys.stdout
    executable = vind_par2_executable() if verificatie_lezer is None else None
    if verificatie_lezer is None:
        if executable is None:
            uitvoer.write(
                "PAR2-tool niet gevonden: stel PAR2_PATH in of installeer "
                "een commandline PAR2-tool.\n"
            )
        verificatie_lezer = (
            lambda par_set: verifieer_par_set(par_set, executable)
        )
    par_sets = detecteer_par_sets(par_map)
    rar_set_keys = [
        rij["rar_set_key"]
        for rij in database.verbinding.execute(
            "SELECT rar_set_key FROM rar_sets WHERE actief = 1"
        )
    ]
    rijen = []
    gekoppelde_rar_sets = set()
    for par_set in par_sets:
        rar_sleutel = koppel_par_aan_rar(
            par_set.par_set_key, rar_set_keys
        )
        if rar_sleutel is None:
            verificatie = ParVerificatie(
                status="NO_RAR",
                melding="Geen eenduidig gekoppelde RAR-set gevonden.",
            )
        else:
            gekoppelde_rar_sets.add(rar_sleutel)
            try:
                verificatie = verificatie_lezer(par_set)
            except Exception as fout:
                verificatie = ParVerificatie(
                    status="UNKNOWN", melding=str(fout)
                )
        rijen.append(_maak_rij(par_set, rar_sleutel, verificatie))

    for rar_sleutel in set(rar_set_keys) - gekoppelde_rar_sets:
        rijen.append({
            "par_set_key": f"__no_par__:{rar_sleutel}",
            "gekoppelde_rar_set_key": rar_sleutel,
            "par_startbestand": None,
            "aantal_par_bestanden": 0,
            "aantal_recovery_volumes": 0,
            "recovery_blocks_beschikbaar": None,
            "recovery_blocks_benodigd": None,
            "status": "NO_PAR",
            "verificatie_tool": None,
            "verificatie_melding": "Geen PAR2-set voor deze RAR-set.",
        })
    _synchroniseer(database, rijen)
    overzicht = verkrijg_par_overzicht(database)
    toon_par_overzicht(overzicht, uitvoer)
    return overzicht


def _maak_rij(par_set, rar_sleutel, verificatie):
    if verificatie.status not in GELDIGE_STATUSSEN:
        raise ValueError(f"Ongeldige PAR-status: {verificatie.status}")
    return {
        "par_set_key": par_set.par_set_key,
        "gekoppelde_rar_set_key": rar_sleutel,
        "par_startbestand": str(par_set.startbestand),
        "aantal_par_bestanden": len(par_set.bestanden),
        "aantal_recovery_volumes": len(par_set.recovery_volumes),
        "recovery_blocks_beschikbaar":
            verificatie.recovery_blocks_beschikbaar,
        "recovery_blocks_benodigd": verificatie.recovery_blocks_benodigd,
        "status": verificatie.status,
        "verificatie_tool": verificatie.tool,
        "verificatie_melding": verificatie.melding,
        "_verificatie": verificatie,
    }


def _synchroniseer(database, rijen):
    nu = datetime.now().isoformat(timespec="seconds")
    database.verbinding.execute("DELETE FROM par_inventory")
    for rij in rijen:
        database.verbinding.execute(
            """
            INSERT INTO par_inventory (
              par_set_key, gekoppelde_rar_set_key, par_startbestand,
              aantal_par_bestanden, aantal_recovery_volumes,
              recovery_blocks_beschikbaar, recovery_blocks_benodigd,
              status, verificatie_tool, verificatie_melding, bijgewerkt_op
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rij["par_set_key"], rij["gekoppelde_rar_set_key"],
                rij["par_startbestand"], rij["aantal_par_bestanden"],
                rij["aantal_recovery_volumes"],
                rij["recovery_blocks_beschikbaar"],
                rij["recovery_blocks_benodigd"], rij["status"],
                rij["verificatie_tool"], rij["verificatie_melding"], nu,
            ),
        )
        verificatie = rij.get("_verificatie")
        if (
            verificatie is not None
            and rij["gekoppelde_rar_set_key"] is not None
        ):
            database.verbinding.execute(
                """
                INSERT INTO par_verifications (
                  par_set_key, executable_path, executable_source,
                  par2_file, command, return_code, verification_status,
                  verification_summary, stdout, stderr, verified_at,
                  duration_ms, timed_out, error_type
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
                    rij["par_set_key"], verificatie.tool,
                    verificatie.tool_source,
                    verificatie.par2_file or rij["par_startbestand"],
                    serialiseer_command(verificatie.command),
                    verificatie.return_code, verificatie.status,
                    verificatie.melding or "Geen samenvatting.",
                    verificatie.stdout, verificatie.stderr,
                    verificatie.verified_at or nu,
                    verificatie.duration_ms, verificatie.timed_out,
                    verificatie.error_type,
                ),
            )
    database.verbinding.commit()


def verkrijg_par_overzicht(database):
    rij = database.verbinding.execute(
        """
        SELECT
          SUM(CASE WHEN aantal_par_bestanden > 0 THEN 1 ELSE 0 END)
            AS par_sets,
          COUNT(DISTINCT CASE WHEN gekoppelde_rar_set_key IS NOT NULL
                              AND aantal_par_bestanden > 0
                         THEN gekoppelde_rar_set_key END)
            AS gekoppelde_rar_sets,
          SUM(CASE WHEN status = 'REPAIRABLE' THEN 1 ELSE 0 END)
            AS repareerbaar,
          SUM(CASE WHEN status IN ('COMPLETE', 'OK') THEN 1 ELSE 0 END)
            AS compleet,
          SUM(CASE WHEN status = 'NOT_REPAIRABLE' THEN 1 ELSE 0 END)
            AS niet_repareerbaar,
          SUM(CASE WHEN status = 'NO_PAR' THEN 1 ELSE 0 END)
            AS geen_par,
          SUM(CASE WHEN status = 'NO_RAR' THEN 1 ELSE 0 END)
            AS geen_rar,
          SUM(CASE WHEN status = 'UNKNOWN' THEN 1 ELSE 0 END)
            AS onbekend
        FROM par_inventory
        """
    ).fetchone()
    overzicht = {sleutel: rij[sleutel] or 0 for sleutel in rij.keys()}
    overzicht["items"] = [
        dict(item)
        for item in database.verbinding.execute(
            """
            SELECT par_set_key, gekoppelde_rar_set_key, status,
                   verificatie_melding
            FROM par_inventory
            WHERE aantal_par_bestanden > 0
            ORDER BY par_set_key
            """
        )
    ]
    return overzicht


def toon_par_overzicht(overzicht, uitvoer=None):
    uitvoer = uitvoer or sys.stdout
    uitvoer.write("\nPAR2-INVENTARIS\n")
    uitvoer.write(f"PAR2-sets             : {overzicht['par_sets']}\n")
    uitvoer.write(
        f"Gekoppelde RAR-sets   : {overzicht['gekoppelde_rar_sets']}\n"
    )
    uitvoer.write(f"Compleet              : {overzicht['compleet']}\n")
    uitvoer.write(f"Repareerbaar          : {overzicht['repareerbaar']}\n")
    uitvoer.write(
        f"Niet repareerbaar     : {overzicht['niet_repareerbaar']}\n"
    )
    uitvoer.write(f"Geen PAR              : {overzicht['geen_par']}\n")
    uitvoer.write(f"Geen RAR              : {overzicht['geen_rar']}\n")
    uitvoer.write(f"Onbekend              : {overzicht['onbekend']}\n")
    # Compact per-setoverzicht zonder omvangrijke procesuitvoer.
    for rij in overzicht.get("items", []):
        uitvoer.write(
            f"[{rij['par_set_key']}] {rij['status']} - "
            f"{rij['verificatie_melding'] or 'Geen samenvatting.'}\n"
        )
