"""Dunne, stille GUI-orchestratie rond de bestaande recovery-backend."""

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from analyse import voer_analyse
from core.progress import WorkflowProgress, maak_progress
from core.salvage_workflow import (
    ontdek_archive_sets,
    voer_salvage_workflow_uit,
)
from core.spotify import sync_playlist, voer_spotify_search_uit
from core.spotify.auth import SpotifyUserAuthorizationError
from core.spotify.client import SpotifyConfigurationError
from core.spotify.search import definitieve_spotify_telling
from database import (
    DATABASE_BESTAND,
    SQLiteDatabase,
    leid_archive_set_name_af,
)
from report import maak_rapport


WORKFLOW_STAGES = (
    "Analyse",
    "PAR2",
    "RAR Recovery",
    "Validatie",
    "Recovery Items",
    "Spotify Search",
    "Playlist Sync",
    "Rapport",
)
SALVAGE_STAGES = WORKFLOW_STAGES[1:5]
_DETAIL_PREFIXES = (
    "0-byte:",
    "FFmpeg [",
    "RAR [",
    "NIET GEVONDEN:",
    "GENORMALISEERD:",
)


class WorkflowLogWriter:
    """Stuur betekenisvolle backendregels naar de GUI, nooit naar stdout."""

    def __init__(self, callback, detail_limit=20):
        self.callback = callback
        self.detail_limit = detail_limit
        self.detail_count = 0
        self.buffer = ""

    def write(self, tekst):
        tekst = str(tekst or "").replace("\r", "\n")
        self.buffer += tekst
        regels = self.buffer.split("\n")
        self.buffer = regels.pop()
        for regel in regels:
            self._emit(regel.strip())
        return len(tekst)

    def _emit(self, regel):
        if not regel:
            return
        if regel.startswith(_DETAIL_PREFIXES):
            if self.detail_count >= self.detail_limit:
                return
            self.detail_count += 1
        self.callback(regel)

    def flush(self):
        if self.buffer.strip():
            self._emit(self.buffer.strip())
        self.buffer = ""


@dataclass(frozen=True)
class WorkflowCallbacks:
    stage_started: Callable[[str], None]
    stage_progress: Callable[[WorkflowProgress], None]
    stage_completed: Callable[[str], None]
    stage_skipped: Callable[[str, str], None]
    stage_failed: Callable[[str, str], None]
    log_message: Callable[[str], None]


def _par2_setnaam(bestand):
    naam = Path(bestand).name
    naam = re.sub(r"(?i)\.vol\d+\+\d+\.par2$", "", naam)
    naam = re.sub(r"(?i)\.par2$", "", naam)
    return naam


def bepaal_recovery_setnaam(source, database_path=DATABASE_BESTAND):
    source = Path(source)
    archive_sets = ontdek_archive_sets(
        source, exclude=source / "megaman_salvage"
    ) if source.is_dir() else ()
    archive_names = tuple(
        leid_archive_set_name_af(set_.main_archive.name)
        for set_ in archive_sets
    )

    database_path = Path(database_path)
    if database_path.is_file():
        database = SQLiteDatabase(database_path)
        try:
            rijen = database.verbinding.execute(
                """
                SELECT archive_set_name
                FROM recovery_sets
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        finally:
            database.sluit()
        for rij in rijen:
            naam = rij["archive_set_name"]
            if not archive_names or any(
                naam.casefold() == kandidaat.casefold()
                for kandidaat in archive_names
            ):
                return naam

    if archive_names:
        return archive_names[0]

    if source.is_dir():
        par2 = next(
            (
                pad for pad in sorted(source.rglob("*"))
                if pad.is_file()
                and pad.name.casefold().endswith(".par2")
                and "megaman_salvage" not in {
                    deel.casefold() for deel in pad.parts
                }
            ),
            None,
        )
        if par2:
            return _par2_setnaam(par2)
    if source.is_file():
        if source.name.casefold().endswith(".par2"):
            return _par2_setnaam(source)
        return leid_archive_set_name_af(source.name)
    return source.name


class _SalvageProgressAdapter:
    def __init__(self, callbacks):
        self.callbacks = callbacks
        self.current = None
        self.final = set()

    def __call__(self, progress):
        stage = progress.stage
        if stage not in SALVAGE_STAGES:
            return
        if stage != self.current:
            self._finish_current()
            doel = SALVAGE_STAGES.index(stage)
            for gemist in SALVAGE_STAGES[:doel]:
                if gemist not in self.final:
                    self.callbacks.stage_skipped(
                        gemist,
                        "Geen afzonderlijke backendactie was nodig.",
                    )
                    self.final.add(gemist)
            self.callbacks.stage_started(stage)
            self.current = stage
        self.callbacks.stage_progress(progress)

    def _finish_current(self):
        if self.current and self.current not in self.final:
            self.callbacks.stage_completed(self.current)
            self.final.add(self.current)
        self.current = None

    def finish(self):
        self._finish_current()
        for stage in SALVAGE_STAGES:
            if stage not in self.final:
                self.callbacks.stage_skipped(
                    stage,
                    "Geen afzonderlijke backendactie was nodig.",
                )
                self.final.add(stage)


class RecoveryGuiWorkflow:
    def __init__(
        self,
        analyse=voer_analyse,
        salvage=voer_salvage_workflow_uit,
        spotify_search=voer_spotify_search_uit,
        playlist_sync=sync_playlist,
        report=maak_rapport,
        database_factory=SQLiteDatabase,
        database_path=DATABASE_BESTAND,
    ):
        self.analyse = analyse
        self.salvage = salvage
        self.spotify_search = spotify_search
        self.playlist_sync = playlist_sync
        self.report = report
        self.database_factory = database_factory
        self.database_path = database_path

    def run(self, source, callbacks):
        source = Path(source).resolve()
        writer = WorkflowLogWriter(callbacks.log_message)
        setnaam = bepaal_recovery_setnaam(source, self.database_path)
        callbacks.log_message(f"Recovery-set bepaald: {setnaam}")
        summary = {
            "recovery_set": setnaam,
            "total_mp3": 0,
            "ok": 0,
            "ffmpeg_errors": 0,
            "zero_byte": 0,
            "recovery_items": 0,
            "matched": 0,
            "low_confidence": 0,
            "manual_review": 0,
            "not_found": 0,
            "spotify_errors": 0,
            "playlist_added": 0,
            "playlist_existing": 0,
            "playlist_id": None,
            "playlist_name": "",
            "report_path": "",
            "status": "mislukt",
        }

        callbacks.stage_started("Analyse")

        def analyse_progress(progress):
            callbacks.stage_progress(
                replace(progress, stage="Analyse")
            )

        analyse_resultaat = self.analyse(
            source,
            source,
            uitvoer=writer,
            progress_callback=analyse_progress,
            console_progress=False,
            include_legacy_spotify=False,
        )
        writer.flush()
        summary.update({
            "total_mp3": analyse_resultaat.totaal_mp3,
            "ok": analyse_resultaat.goed,
            "ffmpeg_errors": analyse_resultaat.ffmpeg_fouten,
            "zero_byte": analyse_resultaat.nul_bytes,
            "report_path": str(analyse_resultaat.rapport_pad),
        })
        callbacks.stage_progress(maak_progress(
            "Analyse", 1, 1, "Analyse voltooid.",
            ok_count=analyse_resultaat.goed,
            ffmpeg_error_count=analyse_resultaat.ffmpeg_fouten,
            zero_byte_count=analyse_resultaat.nul_bytes,
        ))
        callbacks.stage_completed("Analyse")

        workspace = source / "megaman_salvage"
        salvage_adapter = _SalvageProgressAdapter(callbacks)
        resultaten = self.salvage(
            source,
            workspace=workspace,
            uitvoer=writer,
            progress_callback=salvage_adapter,
        )
        writer.flush()
        salvage_adapter.finish()
        if not resultaten:
            raise RuntimeError("De salvage-workflow gaf geen resultaat terug.")
        resultaat = resultaten[0]
        setnaam = bepaal_recovery_setnaam(source, self.database_path)
        summary.update({
            "recovery_set": setnaam,
            "total_mp3": resultaat.fysiek_aanwezig,
            "ok": resultaat.goed,
            "ffmpeg_errors": resultaat.ffmpeg_fouten,
            "zero_byte": resultaat.nul_bytes,
            "recovery_items": resultaat.spotify_recovery_items,
        })
        callbacks.log_message(f"Canonieke recovery-set: {setnaam}")

        database = self.database_factory(self.database_path)
        try:
            callbacks.stage_started("Spotify Search")

            def spotify_progress(progress):
                callbacks.stage_progress(progress)

            try:
                search = self.spotify_search(
                    database,
                    archive_set_name=setnaam,
                    uitvoer=writer,
                    progress_callback=spotify_progress,
                    verbose_items=False,
                )
            except SpotifyConfigurationError as error:
                callbacks.stage_skipped("Spotify Search", str(error))
                callbacks.log_message(
                    f"Spotify Search overgeslagen: {error}"
                )
                search = None
            else:
                telling = definitieve_spotify_telling(
                    database, search.recovery_set_id
                )
                summary.update({
                    "matched": telling["MATCHED"],
                    "low_confidence": telling["LOW_CONFIDENCE"],
                    "manual_review": telling["MANUAL_REVIEW"],
                    "not_found": telling["NOT_FOUND"],
                    "spotify_errors": telling["ERROR"],
                })
                callbacks.stage_progress(maak_progress(
                    "Spotify Search",
                    sum(telling.values()),
                    search.total,
                    "Definitieve Spotify-statussen opgeslagen.",
                    matched_count=telling["MATCHED"],
                    low_confidence_count=telling["LOW_CONFIDENCE"],
                    manual_review_count=telling["MANUAL_REVIEW"],
                    not_found_count=telling["NOT_FOUND"],
                    error_count=telling["ERROR"],
                ))
                callbacks.stage_completed("Spotify Search")
                callbacks.log_message(
                    "Spotify Search voltooid: "
                    f"MATCHED {telling['MATCHED']}, "
                    f"LOW_CONFIDENCE {telling['LOW_CONFIDENCE']}, "
                    f"MANUAL_REVIEW {telling['MANUAL_REVIEW']}, "
                    f"NOT_FOUND {telling['NOT_FOUND']}."
                )
            writer.flush()

            callbacks.stage_started("Playlist Sync")
            if search is None or summary["matched"] <= 0:
                reden = (
                    "Spotify Search was niet beschikbaar."
                    if search is None else
                    "Er zijn geen MATCHED-tracks."
                )
                callbacks.stage_skipped("Playlist Sync", reden)
                callbacks.log_message(
                    f"Playlist Sync overgeslagen: {reden}"
                )
            else:
                try:
                    playlist = self.playlist_sync(
                        database,
                        archive_set_name=setnaam,
                        uitvoer=writer,
                    )
                except (
                    SpotifyConfigurationError,
                    SpotifyUserAuthorizationError,
                ) as error:
                    callbacks.stage_skipped("Playlist Sync", str(error))
                    callbacks.log_message(
                        f"Playlist Sync overgeslagen: {error}"
                    )
                else:
                    summary.update({
                        "playlist_added": playlist.added,
                        "playlist_existing": playlist.already_present,
                        "playlist_id": playlist.playlist_id,
                        "playlist_name": playlist.playlist_name,
                    })
                    callbacks.stage_progress(maak_progress(
                        "Playlist Sync", 1, 1,
                        "Spotify-playlist bijgewerkt.",
                    ))
                    callbacks.stage_completed("Playlist Sync")
                    callbacks.log_message(
                        f"Playlist bijgewerkt: {playlist.playlist_name}."
                    )
            writer.flush()

            callbacks.stage_started("Rapport")
            rapportpad = self.report(source, database)
            summary["report_path"] = str(rapportpad)
            callbacks.stage_progress(maak_progress(
                "Rapport", 1, 1, "Eindrapport gemaakt."
            ))
            callbacks.stage_completed("Rapport")
            callbacks.log_message(f"Rapport gemaakt: {rapportpad}")
        finally:
            database.sluit()

        summary["status"] = (
            "geslaagd"
            if resultaat.eindstatus in {"COMPLETE", "SALVAGED"}
            else "gedeeltelijk geslaagd"
            if resultaat.eindstatus == "PARTIAL"
            else "mislukt"
        )
        return summary
