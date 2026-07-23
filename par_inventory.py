import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from paden import normaliseer_relatief_pad_sleutel


PAR_VOLUME = re.compile(
    r"^(?P<basis>.+)\.vol[^.]+\.par2$",
    re.IGNORECASE,
)
PAR_BASIS = re.compile(r"^(?P<basis>.+)\.par2$", re.IGNORECASE)
GELDIGE_STATUSSEN = {
    "OK", "REPAIRABLE", "NOT_REPAIRABLE",
    "NO_RAR", "NO_PAR", "UNKNOWN",
}
PAR_TOOL_KANDIDATEN = (
    r"C:\Program Files\MultiPar\par2j64.exe",
    r"C:\Program Files\MultiPar\par2j.exe",
    "par2",
    "par2cmdline",
    "par2j64",
    "par2j",
)


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
    for kandidaat in PAR_TOOL_KANDIDATEN:
        pad = Path(kandidaat)
        if pad.is_absolute() and pad.exists():
            return str(pad)
        gevonden = shutil.which(kandidaat)
        if gevonden:
            return gevonden
    return None


def parseer_par_verificatie(uitvoer, tool=None, returncode=0):
    """Parseer gangbare par2cmdline- en MultiPar-verificatiemeldingen."""

    tekst = uitvoer or ""
    beschikbaar = _eerste_getal(
        tekst,
        (
            r"(?i)(\d+)\s+recovery blocks?\s+(?:are\s+)?available",
            r"(?i)recovery blocks?\s+available\s*[:=]\s*(\d+)",
        ),
    )
    benodigd = _eerste_getal(
        tekst,
        (
            r"(?i)(?:you\s+)?need\s+(\d+)\s+(?:more\s+)?"
            r"recovery blocks?",
            r"(?i)recovery blocks?\s+(?:needed|required)\s*[:=]\s*(\d+)",
        ),
    )
    laag = tekst.casefold()
    if (
        "all files are correct" in laag
        or "repair is not required" in laag
        or "all files are complete" in laag
    ):
        status = "OK"
    elif (
        "repair is not possible" in laag
        or "not enough recovery blocks" in laag
        or (
            beschikbaar is not None
            and benodigd is not None
            and beschikbaar < benodigd
        )
    ):
        status = "NOT_REPAIRABLE"
    elif (
        "repair is possible" in laag
        or (
            beschikbaar is not None
            and benodigd is not None
            and benodigd > 0
            and beschikbaar >= benodigd
        )
    ):
        status = "REPAIRABLE"
    else:
        status = "UNKNOWN"
    regels = [regel.strip() for regel in tekst.splitlines() if regel.strip()]
    melding = " | ".join(regels[-5:]) if regels else (
        f"PAR2-tool eindigde met code {returncode} zonder leesbare uitvoer."
    )
    return ParVerificatie(
        status=status,
        recovery_blocks_beschikbaar=beschikbaar,
        recovery_blocks_benodigd=benodigd,
        tool=tool,
        melding=melding,
    )


def _eerste_getal(tekst, patronen):
    for patroon in patronen:
        match = re.search(patroon, tekst)
        if match:
            return int(match.group(1))
    return None


def verifieer_par_set(par_set, tool=None):
    """Voer uitsluitend PAR2-verificatie uit; nooit reparatie."""

    tool = tool or vind_par_tool()
    if not tool:
        return ParVerificatie(
            status="UNKNOWN",
            melding="Geen ondersteunde PAR2-verificatietool gevonden.",
        )
    programmanaam = Path(tool).name.casefold()
    opdracht = (
        [tool, "v", str(par_set.startbestand)]
        if programmanaam.startswith("par2j")
        else [tool, "verify", str(par_set.startbestand)]
    )
    try:
        resultaat = subprocess.run(
            opdracht,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as fout:
        return ParVerificatie(
            status="UNKNOWN", tool=tool, melding=str(fout)
        )
    uitvoer = f"{resultaat.stdout}\n{resultaat.stderr}"
    return parseer_par_verificatie(
        uitvoer, tool=tool, returncode=resultaat.returncode
    )


def voer_par_inventory_uit(
    par_map, database, uitvoer=None, verificatie_lezer=None
):
    """Synchroniseer de actuele read-only PAR2-inventaris."""

    uitvoer = uitvoer or sys.stdout
    verificatie_lezer = verificatie_lezer or verifieer_par_set
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
    return {sleutel: rij[sleutel] or 0 for sleutel in rij.keys()}


def toon_par_overzicht(overzicht, uitvoer=None):
    uitvoer = uitvoer or sys.stdout
    uitvoer.write("\nPAR2-INVENTARIS\n")
    uitvoer.write(f"PAR2-sets             : {overzicht['par_sets']}\n")
    uitvoer.write(
        f"Gekoppelde RAR-sets   : {overzicht['gekoppelde_rar_sets']}\n"
    )
    uitvoer.write(f"Repareerbaar          : {overzicht['repareerbaar']}\n")
    uitvoer.write(
        f"Niet repareerbaar     : {overzicht['niet_repareerbaar']}\n"
    )
    uitvoer.write(f"Geen PAR              : {overzicht['geen_par']}\n")
    uitvoer.write(f"Geen RAR              : {overzicht['geen_rar']}\n")
    uitvoer.write(f"Onbekend              : {overzicht['onbekend']}\n")
