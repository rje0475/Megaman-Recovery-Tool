import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.external_tools import detecteer_7zip, detecteer_winrar
from core.salvage_classification import classificeer_salvage_resultaat
from core.salvage_compare import vergelijk_extractie
from core.salvage_extractor import salvage_extract, winrar_salvage_extract
from core.winrar_recovery import (
    classificeer_archive_set,
    voer_winrar_recovery_uit,
)
from database import DATABASE_BESTAND, SQLiteDatabase
from paden import normaliseer_relatief_pad_sleutel
from rar_inventory import groepeer_rar_sets


@dataclass(frozen=True)
class ArchiveSet:
    sleutel: str
    main_archive: Path
    volumes: tuple[Path, ...]


@dataclass(frozen=True)
class SalvageSamenvatting:
    rar_setnaam: str
    par2_resultaat: str
    winrar_resultaat: str
    extractie_resultaat: str
    verwacht: int
    fysiek_aanwezig: int
    goed: int
    beschadigd_aanwezig: int
    ontbrekend: int
    nul_bytes: int
    onleesbaar: int
    ffmpeg_fouten: int
    duplicaten_verwijderd: int
    grootteafwijking: int
    extra: int
    spotify_recovery_items: int
    eindstatus: str
    extractiemap: Path


class SalvageFout(RuntimeError):
    pass


def _toon_commando(commando):
    return subprocess.list2cmdline(list(commando)) if commando else "(geen)"


def _mp3_stand(extractiemap):
    mp3s = tuple(
        pad for pad in Path(extractiemap).rglob("*")
        if pad.is_file() and pad.suffix.casefold() == ".mp3"
    )
    nul = sum(
        1 for pad in mp3s
        if pad.stat().st_size == 0
    )
    return len(mp3s), nul


def _tooluitvoer_samenvatting(resultaat, limiet=1200):
    tekst = "\n".join(
        deel.strip()
        for deel in (
            getattr(resultaat, "stdout", ""),
            getattr(resultaat, "stderr", ""),
            getattr(resultaat, "foutmelding", ""),
        )
        if deel and deel.strip()
    )
    if not tekst:
        return "(geen toolmelding)"
    tekst = " | ".join(regel.strip() for regel in tekst.splitlines() if regel.strip())
    return tekst[:limiet] + ("…" if len(tekst) > limiet else "")


def _extractiefout_categorie(resultaat):
    tekst = _tooluitvoer_samenvatting(resultaat).casefold()
    if any(term in tekst for term in (
        "next volume", "missing volume", "cannot find volume",
        "volgende volume", "ontbrekend volume",
    )):
        return "ONTBREKEND_VERVOLGVOLUME"
    if any(term in tekst for term in (
        "cannot open", "could not open", "failed to open", "niet openen",
    )):
        return "BRON_NIET_GEOPEND"
    if any(term in tekst for term in (
        "crc failed", "crc error", "checksum error", "data error",
    )):
        return "CRC_OF_DATAFOUT"
    if getattr(resultaat, "exitcode", 0) not in (0, None):
        return "ANDERE_TOOLFOUT"
    return "GEEN"


def _ontbrekende_delen_tekst(nummers, limiet=20):
    nummers = tuple(nummers)
    if not nummers:
        return "geen"
    begin = ", ".join(str(nummer) for nummer in nummers[:limiet])
    rest = len(nummers) - limiet
    return begin + (f" (+{rest} meer)" if rest > 0 else "")


def _log_extractiepoging(
    uitvoer, bron, toolnaam, resultaat, voor, na
):
    uitvoer.write(
        f"Tool: {toolnaam}\n"
        f"Exitcode: {getattr(resultaat, 'exitcode', None)}\n"
        f"Status: {resultaat.status}\n"
        f"MP3's vóór poging: {voor[0]}\n"
        f"MP3's na poging: {na[0]}\n"
        f"Nieuw teruggewonnen: {max(0, na[0] - voor[0])}\n"
        f"Nieuwe nul-byte bestanden: {max(0, na[1] - voor[1])}\n"
        f"Foutcategorie: {_extractiefout_categorie(resultaat)}\n"
        f"Tooluitvoer: {_tooluitvoer_samenvatting(resultaat)}\n"
    )


def ontdek_archive_sets(bronmap, exclude=None):
    bronmap = Path(bronmap)
    exclude = Path(exclude).resolve() if exclude else None
    sets, gebruikt = [], set()

    for rar_set in groepeer_rar_sets(bronmap):
        volumes = tuple(
            volume.resolve()
            for volume in rar_set.volumes
            if not _is_uitgesloten_volume(volume, exclude)
        )
        if not volumes:
            continue
        main_archive = rar_set.startbestand.resolve()
        gebruikt.update(volumes)
        sets.append(ArchiveSet(
            rar_set.rar_set_key, main_archive, volumes
        ))

    oud_pattern = re.compile(r"^(?P<naam>.+)\.rar$", re.IGNORECASE)
    volume_pattern = re.compile(r"^(?P<naam>.+)\.r(?P<deel>\d+)$", re.IGNORECASE)
    for bestand in bronmap.rglob("*"):
        if not bestand.is_file() or _is_uitgesloten_volume(bestand, exclude):
            continue
        match = oud_pattern.match(bestand.name)
        if not match or bestand.resolve() in gebruikt:
            continue
        naam = match.group("naam")
        rvolumes = []
        for kandidaat in bestand.parent.iterdir():
            volume_match = volume_pattern.match(kandidaat.name)
            if (
                kandidaat.is_file()
                and volume_match
                and volume_match.group("naam").casefold() == naam.casefold()
                and not _is_uitgesloten_volume(kandidaat, exclude)
            ):
                rvolumes.append(kandidaat.resolve())
        volumes = (bestand.resolve(), *sorted(
            rvolumes, key=_volume_sorteersleutel
        ))
        basis = bestand.relative_to(bronmap).with_suffix("")
        gebruikt.update(volumes)
        sets.append(ArchiveSet(
            normaliseer_relatief_pad_sleutel(basis),
            bestand.resolve(), volumes,
        ))
    return tuple(sorted(sets, key=lambda item: item.sleutel.casefold()))


def _is_uitgesloten_volume(pad, exclude=None):
    pad = Path(pad)
    naam = pad.name.casefold()
    if naam.endswith(".old") or naam.startswith(("rebuilt.", "repaired.")):
        return True
    if exclude:
        try:
            return pad.resolve().is_relative_to(Path(exclude).resolve())
        except (OSError, ValueError):
            return False
    return False


def _volume_sorteersleutel(pad):
    naam = Path(pad).name
    part = re.search(r"\.part(\d+)\.rar$", naam, re.IGNORECASE)
    if part:
        return (0, int(part.group(1)), naam.casefold())
    oud = re.search(r"\.r(\d+)$", naam, re.IGNORECASE)
    if oud:
        return (1, int(oud.group(1)) + 1, naam.casefold())
    return (1, 0, naam.casefold())


def _resolveer_set_volumes(set_, bronmap, workspace, uitvoer):
    bronmap = Path(bronmap).resolve()
    workspace = Path(workspace).resolve()
    model_volumes = tuple(set_.volumes or ())
    bruikbaar = []
    for volume in model_volumes:
        volume = Path(volume)
        if not volume.is_absolute():
            volume = bronmap / volume
        if volume.is_file() and not _is_uitgesloten_volume(volume, workspace):
            bruikbaar.append(volume.resolve())
    bruikbaar = tuple(sorted(set(bruikbaar), key=_volume_sorteersleutel))

    opnieuw = ontdek_archive_sets(bronmap, exclude=workspace)
    kandidaat = next(
        (
            item for item in opnieuw
            if item.sleutel.casefold() == set_.sleutel.casefold()
        ),
        None,
    )
    if kandidaat is None and set_.main_archive:
        main_naam = Path(set_.main_archive).name.casefold()
        kandidaat = next(
            (
                item for item in opnieuw
                if item.main_archive.name.casefold() == main_naam
            ),
            None,
        )
    fallback = kandidaat.volumes if kandidaat else ()
    volumes = fallback if len(fallback) > len(bruikbaar) else bruikbaar
    genegeerd_old = sum(
        1 for pad in bronmap.rglob("*")
        if pad.is_file()
        and pad.name.casefold().endswith(".old")
        and not pad.resolve().is_relative_to(workspace)
    )
    uitvoer.write(
        "RAR-volumecontrole: "
        f"bronmap={bronmap}; set={set_.sleutel}; "
        f"main={set_.main_archive}; model={len(model_volumes)}; "
        f"fallback={len(fallback)}; .old genegeerd={genegeerd_old}\n"
    )
    if volumes:
        uitvoer.write(
            f"RAR-volumes: eerste={volumes[0].name}; "
            f"laatste={volumes[-1].name}\n"
        )
    if not volumes:
        raise SalvageFout("RAR-set bevat geen volumes.")
    main_archive = (
        kandidaat.main_archive
        if kandidaat and kandidaat.main_archive in volumes
        else (Path(set_.main_archive).resolve() if set_.main_archive else volumes[0])
    )
    if main_archive not in volumes:
        main_archive = volumes[0]
    return ArchiveSet(set_.sleutel, main_archive, tuple(volumes))


def _identiteit(intern_pad):
    stam = Path(str(intern_pad).replace("\\", "/")).stem
    stam = re.sub(r"^\d+\s*[-._]\s*", "", stam)
    delen = re.split(r"\s+[-–—]\s+", stam, maxsplit=1)
    return (delen[0], delen[1]) if len(delen) == 2 else (None, stam)


def _synchroniseer_recovery(database, set_sleutel, vergelijking):
    defecten = {
        normaliseer_relatief_pad_sleutel(item.intern_pad): item
        for item in vergelijking.items
        if item.status in ("MISSING", "ZERO_BYTE", "UNREADABLE")
    }
    for item in defecten.values():
        artiest, titel = _identiteit(item.intern_pad)
        probleem_type = (
            "ontbreekt"
            if item.status == "MISSING"
            else "corrupt"
            if item.status == "UNREADABLE" or item.ffmpeg_fout
            else "nul_bytes"
        )
        feit_corrupt = (
            item.status == "UNREADABLE" or bool(item.ffmpeg_fout)
        )
        database.verbinding.execute(
            """
            INSERT INTO recovery_items (
              rar_set_key, verwacht_rel_pad, verwacht_rel_pad_norm,
              probleem_type, probleem_bron, verwachte_grootte, ffmpeg_fout,
              feit_ontbreekt, feit_corrupt, feit_nul_bytes,
              spotify_verwerkt, download_verwerkt, geplaatst,
              bepaalde_artiest, bepaalde_titel, identiteit_bron,
              identiteit_betrouwbaarheid, identiteit_reden,
              aangemaakt_op, bijgewerkt_op
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?,
                      'rar_pad', .75, ?, ?, ?)
            ON CONFLICT (rar_set_key, verwacht_rel_pad_norm) DO UPDATE SET
              probleem_type=excluded.probleem_type,
              probleem_bron=excluded.probleem_bron,
              verwachte_grootte=excluded.verwachte_grootte,
              ffmpeg_fout=excluded.ffmpeg_fout,
              feit_ontbreekt=excluded.feit_ontbreekt,
              feit_corrupt=excluded.feit_corrupt,
              feit_nul_bytes=excluded.feit_nul_bytes,
              bepaalde_artiest=COALESCE(recovery_items.bepaalde_artiest,
                                        excluded.bepaalde_artiest),
              bepaalde_titel=COALESCE(recovery_items.bepaalde_titel,
                                      excluded.bepaalde_titel),
              identiteit_reden=excluded.identiteit_reden,
              bijgewerkt_op=excluded.bijgewerkt_op
            """,
            (
                set_sleutel, item.intern_pad,
                normaliseer_relatief_pad_sleutel(item.intern_pad),
                probleem_type, ",".join(item.bronnen or ("salvage",)),
                item.verwachte_grootte, item.ffmpeg_fout,
                item.status == "MISSING", feit_corrupt,
                item.status == "ZERO_BYTE", artiest, titel, item.reden,
                datetime.now().isoformat(timespec="seconds"),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
    for rij in database.verbinding.execute(
        """
        SELECT id, verwacht_rel_pad_norm FROM recovery_items
        WHERE rar_set_key=? AND probleem_bron LIKE '%salvage%'
        """, (set_sleutel,)
    ).fetchall():
        if rij["verwacht_rel_pad_norm"] in defecten:
            continue
        beschermd = database.verbinding.execute(
            """
            SELECT 1 FROM spotify_smart_results
            WHERE recovery_item_id=? AND status IN ('MANUAL','REVIEWED_NONE')
            """, (rij["id"],)
        ).fetchone()
        if not beschermd:
            database.verbinding.execute(
                "DELETE FROM recovery_items WHERE id=?", (rij["id"],)
            )
    database.verbinding.commit()
    return len(defecten)


def _bewaar_run(
    database, gestart, samenvatting, bronstatus, winrar, extractie,
    vergelijking, recovery_workspace, gekozen, winrar_tool, zeven_tool,
):
    klaar = datetime.now().isoformat(timespec="seconds")
    cursor = database.verbinding.execute(
        """
        INSERT INTO salvage_runs (
          started_at, finished_at, rar_set_key, source_status, par2_result,
          winrar_result, winrar_path, sevenzip_result, sevenzip_path,
          chosen_archive, recovery_workspace, extraction_dir, expected_count,
          physical_count, ok_count, damaged_count, missing_count,
          zero_byte_count, unreadable_count, ffmpeg_error_count,
          deduplicated_count,
          size_mismatch_count, extra_count, recovery_item_count,
          final_status, summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?)
        """,
        (
            gestart, klaar, samenvatting.rar_setnaam, bronstatus,
            samenvatting.par2_resultaat, winrar.status,
            str(winrar_tool.pad) if winrar_tool.pad else None,
            extractie.status, str(zeven_tool.pad) if zeven_tool.pad else None,
            str(gekozen), str(recovery_workspace),
            str(samenvatting.extractiemap), samenvatting.verwacht,
            samenvatting.fysiek_aanwezig, samenvatting.goed,
            samenvatting.beschadigd_aanwezig, samenvatting.ontbrekend,
            samenvatting.nul_bytes, samenvatting.onleesbaar,
            samenvatting.ffmpeg_fouten,
            samenvatting.duplicaten_verwijderd,
            samenvatting.grootteafwijking, samenvatting.extra,
            samenvatting.spotify_recovery_items, samenvatting.eindstatus,
            repr(samenvatting),
        ),
    )
    for item in vergelijking.items:
        database.verbinding.execute(
            """
            INSERT INTO salvage_file_results (
              salvage_run_id, internal_path, extracted_path, status,
              expected_size, actual_size, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                cursor.lastrowid, item.intern_pad,
                str(item.bestand) if item.bestand else None, item.status,
                item.verwachte_grootte, item.werkelijke_grootte, item.reden,
            )
        )
    for extra in vergelijking.extras:
        database.verbinding.execute(
            """
            INSERT INTO salvage_file_results (
              salvage_run_id, internal_path, extracted_path, status, reason
            ) VALUES (?, ?, ?, 'EXTRA', 'extra_after_salvage')
            """, (cursor.lastrowid, extra.name, str(extra))
        )
    database.verbinding.commit()


def voer_salvage_workflow_uit(
    bronmap, workspace=None, rar_set=None, skip_par2=False,
    skip_winrar=False, no_spotify=False, database_pad=DATABASE_BESTAND,
    uitvoer=None, winrar_runner=None, sevenzip_runner=None,
):
    uitvoer = uitvoer or sys.stdout
    bronmap = Path(bronmap).resolve()
    if not bronmap.is_dir():
        raise SalvageFout(f"Bronmap bestaat niet: {bronmap}")
    if not Path(database_pad).is_file():
        raise SalvageFout(f"Database bestaat niet: {Path(database_pad).resolve()}")
    workspace = (
        Path(workspace).resolve() if workspace
        else bronmap / "megaman_salvage"
    )
    uitvoer.write("Salvage-workflow gestart\n")
    uitvoer.write(f"Werkmap: {workspace}\n")
    sets = ontdek_archive_sets(bronmap, exclude=workspace)
    if rar_set:
        sets = tuple(s for s in sets if s.sleutel.casefold() == rar_set.casefold())
    if not sets:
        raise SalvageFout("Geen passende RAR-sets gevonden.")
    database = SQLiteDatabase(database_pad)
    winrar_tool, zeven_tool = detecteer_winrar(), detecteer_7zip()
    samenvattingen = []
    try:
        # De bestaande repairservice verwerkt alle REPAIRABLE sets eenmalig.
        if not skip_par2:
            aantal = database.verbinding.execute(
                "SELECT COUNT(*) aantal FROM par_inventory WHERE status='REPAIRABLE'"
            ).fetchone()["aantal"]
            if aantal:
                try:
                    from par2_repair import voer_par2_reparatie_uit
                    voer_par2_reparatie_uit(
                        bronmap, database_pad=database_pad, uitvoer=uitvoer
                    )
                    uitvoer.write("PAR2-reparatie resultaat: voltooid\n")
                except Exception as fout:
                    uitvoer.write(f"PAR2-reparatie resultaat: mislukt ({fout})\n")
        for set_ in sets:
            set_ = _resolveer_set_volumes(
                set_, bronmap, workspace, uitvoer
            )
            gestart = datetime.now().isoformat(timespec="seconds")
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            rij = database.verbinding.execute(
                """
                SELECT status FROM par_inventory
                WHERE gekoppelde_rar_set_key=? OR par_set_key=?
                LIMIT 1
                """, (set_.sleutel, set_.sleutel)
            ).fetchone()
            bronstatus = rij["status"] if rij else "UNKNOWN"
            set_workspace = workspace / re.sub(r"[^a-zA-Z0-9._-]+", "_", set_.sleutel)
            recovery = set_workspace / "recovery" / run_id
            extracted = set_workspace / "extracted"
            uitvoer.write(
                f"\nSALVAGE [{set_.sleutel}] PAR2={bronstatus}\n"
            )
            uitvoer.write(
                f"Originele RAR-volumes gevonden: {len(set_.volumes)}\n"
                f"PAR2-status: {bronstatus}\n"
            )
            if bronstatus == "NOT_REPAIRABLE":
                uitvoer.write(
                    "PAR2 onvoldoende; WinRAR/7-Zip salvage wordt geprobeerd.\n"
                )
            if not skip_winrar and bronstatus != "COMPLETE":
                uitvoer.write(
                    "WinRAR/RAR executable: "
                    f"{winrar_tool.pad or 'niet gevonden'}\n"
                    "WinRAR recovery commando gestart\n"
                )
                kwargs = {"tool": winrar_tool}
                if winrar_runner:
                    kwargs["runner"] = winrar_runner
                winrar = voer_winrar_recovery_uit(
                    set_.volumes, recovery, **kwargs
                )
                uitvoer.write(
                    f"WinRAR recovery commando: "
                    f"{_toon_commando(getattr(winrar, 'commando', ()))}\n"
                    f"WinRAR recovery exitcode: "
                    f"{getattr(winrar, 'exitcode', None)}\n"
                    f"WinRAR recovery: {winrar.status}\n"
                    f"Rebuilt volumes gevonden: "
                    f"{len(getattr(winrar, 'herstelde_volumes', ()))}\n"
                )
            else:
                from core.winrar_recovery import WinRarResultaat
                winrar = WinRarResultaat(
                    "NOT_APPLICABLE", set_.volumes, recovery,
                    set_.main_archive, None, "", "", (), (),
                    set_.main_archive, None,
                )
            verwacht_volumeaantal = len(set_.volumes)
            herstelde_sets = tuple(
                getattr(winrar, "herstelde_sets", ()) or ()
            )
            if not herstelde_sets and getattr(
                winrar, "herstelde_volumes", ()
            ):
                herstelde_sets = (
                    classificeer_archive_set(
                        winrar.herstelde_volumes,
                        verwacht_volumeaantal,
                        "rebuilt",
                    ),
                )
            origineel = classificeer_archive_set(
                set_.volumes, verwacht_volumeaantal, "origineel"
            )
            complete_hersteld = tuple(
                bron for bron in herstelde_sets
                if bron.classificatie == "COMPLETE"
            )
            aanvullende_bronnen = tuple(
                bron for bron in herstelde_sets
                if bron.classificatie in ("PARTIAL", "SINGLE_VOLUME")
            )
            ongeldige_bronnen = tuple(
                bron for bron in herstelde_sets
                if bron.classificatie == "INVALID"
            )
            # Herstelde bronnen kunnen unieke bestanden opleveren, maar de
            # originele set wordt altijd daarna eveneens geprobeerd.
            salvagebronnen = complete_hersteld + aanvullende_bronnen + (origineel,)
            for bron in ongeldige_bronnen:
                uitvoer.write(
                    f"Salvagebron {bron.soort}: INVALID; overgeslagen; "
                    f"ontbrekende volumes: "
                    f"{_ontbrekende_delen_tekst(bron.ontbrekende_delen)}\n"
                )
            uitvoer.write(
                f"Salvagebronnen gepland: {len(salvagebronnen)}\n"
            )
            winrar_kwargs = {"tool": winrar_tool}
            if winrar_runner:
                winrar_kwargs["runner"] = winrar_runner
            sevenzip_kwargs = {"tool": zeven_tool}
            if sevenzip_runner:
                sevenzip_kwargs["runner"] = sevenzip_runner
            extractie = None
            gekozen = set_.main_archive
            for bron in salvagebronnen:
                gekozen = bron.eerste_volume
                uitvoer.write(
                    f"\nSalvagebron: {bron.soort}\n"
                    f"Classificatie bron: {bron.classificatie}\n"
                    f"Aantal volumes in bron: {len(bron.volumes)}\n"
                    f"Verwacht aantal volumes: {bron.verwacht_aantal}\n"
                    f"Eerste volume: {bron.eerste_volume}\n"
                    f"Ontbrekende volumes: "
                    f"{_ontbrekende_delen_tekst(bron.ontbrekende_delen)}\n"
                )
                voor = _mp3_stand(extracted)
                uitvoer.write("WinRAR-extractie gestart\n")
                winrar_extractie = winrar_salvage_extract(
                    bron.eerste_volume, extracted, **winrar_kwargs
                )
                na = _mp3_stand(extracted)
                uitvoer.write(
                    f"WinRAR-extractie commando: "
                    f"{_toon_commando(getattr(winrar_extractie, 'commando', ()))}\n"
                )
                _log_extractiepoging(
                    uitvoer, bron, "RAR/WinRAR", winrar_extractie, voor, na
                )

                voor = na
                uitvoer.write(
                    f"7-Zip executable: {zeven_tool.pad or 'niet gevonden'}\n"
                    "7-Zip salvage-extractie gestart\n"
                )
                extractie = salvage_extract(
                    bron.eerste_volume, extracted, **sevenzip_kwargs
                )
                na = _mp3_stand(extracted)
                uitvoer.write(
                    f"7-Zip salvage-extractie commando: "
                    f"{_toon_commando(getattr(extractie, 'commando', ()))}\n"
                )
                _log_extractiepoging(
                    uitvoer, bron, "7-Zip", extractie, voor, na
                )
            if extractie is None:
                raise SalvageFout("Geen bruikbare salvagebronnen gevonden.")
            verwacht = [
                dict(r) for r in database.verbinding.execute(
                    """
                    SELECT verwacht_rel_pad, verwachte_grootte
                    FROM rar_inventory_items WHERE rar_set_key=?
                    """, (set_.sleutel,)
                )
            ]
            ruwe_vergelijking = vergelijk_extractie(verwacht, extracted)
            analyse_rijen = database.verbinding.execute(
                """
                SELECT relatief_pad, bestand, nul_bytes, ffmpeg_status,
                       ffmpeg_type, ffmpeg_melding, rar_status, rar_type
                FROM mp3_bestanden
                WHERE bestaat=1
                  AND (
                    nul_bytes=1 OR ffmpeg_status='ERROR' OR rar_status='ERROR'
                  )
                """
            ).fetchall()
            classificatie = classificeer_salvage_resultaat(
                ruwe_vergelijking, analyse_rijen,
                wortels=(bronmap, workspace, extracted),
            )
            vergelijking = classificatie.vergelijking
            gevonden_mp3s = sum(
                1 for pad in extracted.rglob("*")
                if pad.is_file() and pad.suffix.casefold() == ".mp3"
            )
            uitvoer.write(
                "Extracted-map opnieuw gescand\n"
                f"Uitgepakte MP3's gevonden: {gevonden_mp3s}\n"
            )
            recovery_items = _synchroniseer_recovery(
                database, set_.sleutel, vergelijking
            )
            goed = classificatie.volledig_goed
            ontbrekend = classificatie.ontbrekend
            nul = classificatie.nul_bytes
            onleesbaar = classificatie.onleesbaar
            grootte = vergelijking.aantal("SIZE_MISMATCH")
            defect = ontbrekend + classificatie.beschadigd_aanwezig
            bruikbaar = goed + len(vergelijking.extras)
            if verwacht and defect == 0:
                eind = "COMPLETE" if bronstatus == "COMPLETE" else "SALVAGED"
            elif bruikbaar:
                eind = "PARTIAL"
            else:
                eind = "FAILED"
            samenvatting = SalvageSamenvatting(
                set_.sleutel, bronstatus, winrar.status, extractie.status,
                len(verwacht), classificatie.fysiek_aanwezig, goed,
                classificatie.beschadigd_aanwezig, ontbrekend, nul,
                onleesbaar, classificatie.ffmpeg_fouten_ingelezen,
                classificatie.duplicaten_verwijderd, grootte,
                len(vergelijking.extras), recovery_items, eind, extracted,
            )
            _bewaar_run(
                database, gestart, samenvatting, bronstatus, winrar,
                extractie, vergelijking, recovery, gekozen,
                winrar_tool, zeven_tool,
            )
            uitvoer.write(
                f"{eind}: verwacht {len(verwacht)}, fysiek aanwezig "
                f"{classificatie.fysiek_aanwezig}, volledig goed {goed}, "
                f"ontbrekend {ontbrekend}, 0-byte {nul}, "
                f"onleesbaar {onleesbaar}, extra {len(vergelijking.extras)}\n"
                f"Verwacht: {len(verwacht)}\n"
                f"Fysiek aanwezig: {classificatie.fysiek_aanwezig}\n"
                f"Volledig goed: {goed}\n"
                f"Beschadigd maar aanwezig: "
                f"{classificatie.beschadigd_aanwezig}\n"
                f"FFmpeg-fouten ingelezen: "
                f"{classificatie.ffmpeg_fouten_ingelezen}\n"
                f"Nul-byte: {nul}\n"
                f"Onleesbaar: {onleesbaar}\n"
                f"Leesbare grootteafwijking: {grootte}\n"
                f"Definitief ontbrekend: {ontbrekend}\n"
                f"Duplicaten verwijderd: "
                f"{classificatie.duplicaten_verwijderd}\n"
                f"Definitieve recovery-items: {recovery_items}\n"
            )
            samenvattingen.append(samenvatting)
    finally:
        database.sluit()
    return tuple(samenvattingen)
