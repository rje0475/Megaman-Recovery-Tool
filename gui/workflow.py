"""Dunne GUI-orchestratie rond de bestaande recovery-backend."""

from dataclasses import dataclass
from pathlib import Path

from analyse import voer_analyse
from core.salvage_workflow import voer_salvage_workflow_uit
from core.spotify import sync_playlist, voer_spotify_search_uit
from core.spotify.client import (
    SpotifyConfigurationError,
)
from core.spotify.auth import SpotifyUserAuthorizationError
from database import DATABASE_BESTAND, SQLiteDatabase
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


class WorkflowLogWriter:
    def __init__(self, callback):
        self.callback = callback

    def write(self, tekst):
        if tekst:
            self.callback(str(tekst))
        return len(tekst or "")

    def flush(self):
        return None


@dataclass(frozen=True)
class WorkflowCallbacks:
    stage_started: callable
    stage_progress: callable
    stage_completed: callable
    stage_skipped: callable
    stage_failed: callable
    log_message: callable


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

    def _start(self, callbacks, stage, index, message):
        callbacks.stage_started(stage)
        callbacks.stage_progress(
            stage, index, len(WORKFLOW_STAGES), message
        )
        callbacks.log_message(message)

    def _complete(self, callbacks, stage):
        callbacks.stage_completed(stage)

    def _spotify_counts(self, database, archive_set_name):
        rijen = database.verbinding.execute(
            """
            SELECT r.spotify_status, COUNT(*) aantal
            FROM recovery_items r
            JOIN recovery_sets s ON s.id=r.recovery_set_id
            WHERE s.archive_set_name=? COLLATE NOCASE
              AND r.probleem_bron LIKE '%salvage%'
            GROUP BY r.spotify_status
            """,
            (archive_set_name,),
        ).fetchall()
        return {
            rij["spotify_status"]: rij["aantal"] for rij in rijen
        }

    def run(self, source, callbacks):
        source = Path(source).resolve()
        writer = WorkflowLogWriter(callbacks.log_message)
        summary = {
            "recovery_set": "",
            "recovery_items": 0,
            "matched": 0,
            "low_confidence": 0,
            "manual_review": 0,
            "not_found": 0,
            "playlist_added": 0,
            "playlist_existing": 0,
            "playlist_id": None,
            "playlist_name": "",
            "status": "mislukt",
        }

        self._start(
            callbacks, "Analyse", 0,
            f"Analyse gestart voor {source}",
        )
        self.analyse(source, source, uitvoer=writer)
        self._complete(callbacks, "Analyse")

        workspace = source / "megaman_salvage"
        self._start(
            callbacks, "PAR2", 1,
            "PAR2- en salvagefase gestart.",
        )
        resultaten = self.salvage(
            source, workspace=workspace, uitvoer=writer
        )
        self._complete(callbacks, "PAR2")

        for index, stage in enumerate(
            ("RAR Recovery", "Validatie", "Recovery Items"), start=2
        ):
            self._start(
                callbacks, stage, index,
                f"{stage} uit bestaande salvage-uitkomst verwerkt.",
            )
            self._complete(callbacks, stage)

        if not resultaten:
            raise RuntimeError("De salvage-workflow gaf geen resultaat terug.")
        resultaat = resultaten[0]
        summary["recovery_set"] = resultaat.rar_setnaam
        summary["recovery_items"] = resultaat.spotify_recovery_items

        database = self.database_factory(self.database_path)
        try:
            self._start(
                callbacks, "Spotify Search", 5,
                f"Spotify Search voor {resultaat.rar_setnaam}.",
            )
            try:
                search = self.spotify_search(
                    database,
                    archive_set_name=resultaat.rar_setnaam,
                    uitvoer=writer,
                )
            except SpotifyConfigurationError as error:
                callbacks.stage_skipped("Spotify Search", str(error))
                callbacks.log_message(
                    f"Spotify Search overgeslagen: {error}"
                )
                search = None
            else:
                telling = self._spotify_counts(
                    database, resultaat.rar_setnaam
                )
                summary.update({
                    "matched": telling.get("MATCHED", 0),
                    "low_confidence": telling.get(
                        "LOW_CONFIDENCE", 0
                    ),
                    "manual_review": telling.get(
                        "MANUAL_REVIEW", 0
                    ),
                    "not_found": telling.get("NOT_FOUND", 0),
                })
                self._complete(callbacks, "Spotify Search")

            self._start(
                callbacks, "Playlist Sync", 6,
                "Spotify-playlist synchroniseren.",
            )
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
                        archive_set_name=resultaat.rar_setnaam,
                        uitvoer=writer,
                    )
                except (
                    SpotifyConfigurationError,
                    SpotifyUserAuthorizationError,
                ) as error:
                    callbacks.stage_skipped(
                        "Playlist Sync", str(error)
                    )
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
                    self._complete(callbacks, "Playlist Sync")

            self._start(
                callbacks, "Rapport", 7,
                "Eindrapport opbouwen.",
            )
            rapportpad = self.report(source, database)
            callbacks.log_message(f"Rapport: {rapportpad}")
            self._complete(callbacks, "Rapport")
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
