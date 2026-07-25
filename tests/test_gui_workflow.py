import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.progress import maak_progress
from core.spotify.client import SpotifyConfigurationError
from database import (
    maak_database,
    verkrijg_of_maak_recovery_set,
)
from gui.workflow import (
    RecoveryGuiWorkflow,
    WorkflowCallbacks,
    WORKFLOW_STAGES,
    bepaal_recovery_setnaam,
)


class EventRecorder:
    def __init__(self):
        self.started = []
        self.progress = []
        self.completed = []
        self.skipped = []
        self.failed = []
        self.logs = []
        self.reviews = []

    def callbacks(self):
        return WorkflowCallbacks(
            stage_started=self.started.append,
            stage_progress=self.progress.append,
            stage_completed=self.completed.append,
            stage_skipped=lambda *args: self.skipped.append(args),
            stage_failed=lambda *args: self.failed.append(args),
            log_message=self.logs.append,
            review_requested=lambda summary: (
                self.reviews.append(summary) or True
            ),
        )


class GuiWorkflowAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="4fe20a254f204822ed17e7c3 (1).#3."
        )
        self.root = Path(self.temp.name)
        (self.root / "Megaman2007.part01.rar").touch()
        (self.root / "Megaman2007.part02.rar").touch()
        self.database_path = self.root / "workflow.db"
        database = maak_database(self.database_path)
        self.set_id = verkrijg_of_maak_recovery_set(
            database, "Megaman2007.part01.rar"
        )
        now = "2026-01-01T00:00:00"
        database.verbinding.execute(
            """
            INSERT INTO recovery_items (
              rar_set_key, recovery_set_id,
              verwacht_rel_pad, verwacht_rel_pad_norm,
              probleem_type, probleem_bron,
              feit_ontbreekt, spotify_verwerkt,
              download_verwerkt, geplaatst,
              bepaalde_artiest, bepaalde_titel,
              spotify_track_id, spotify_uri, spotify_status,
              aangemaakt_op, bijgewerkt_op
            ) VALUES (
              'megaman2007', ?, 'track.mp3', 'track.mp3',
              'corrupt', 'salvage', 0, 0, 0, 0,
              'Artist', 'Track', 'track-id',
              'spotify:track:track-id', 'MATCHED', ?, ?
            )
            """,
            (self.set_id, now, now),
        )
        database.verbinding.commit()
        database.sluit()

    def tearDown(self):
        self.temp.cleanup()

    def _analyse(self, *args, **kwargs):
        self.assertFalse(kwargs["console_progress"])
        self.assertFalse(kwargs["include_legacy_spotify"])
        kwargs["progress_callback"](maak_progress(
            "Validatie", 1, 1, "Analysebestand gecontroleerd.",
            ok_count=1, ffmpeg_error_count=0, zero_byte_count=0,
        ))
        kwargs["uitvoer"].write("Analysebackend voltooid\n")
        return SimpleNamespace(
            totaal_mp3=1,
            goed=1,
            ffmpeg_fouten=0,
            nul_bytes=0,
            rapport_pad=self.root / "analyse.txt",
        )

    def _salvage(self, *args, **kwargs):
        progress = kwargs["progress_callback"]
        progress(maak_progress("PAR2", 1, 1, "PAR2 gecontroleerd."))
        progress(maak_progress(
            "RAR Recovery", 1, 1, "RAR-recovery voltooid."
        ))
        progress(maak_progress(
            "Validatie", 1, 1, "Salvagevalidatie voltooid.",
            ok_count=1, ffmpeg_error_count=0, zero_byte_count=0,
        ))
        progress(maak_progress(
            "Recovery Items", 1, 1, "Eén recovery-item.",
        ))
        kwargs["uitvoer"].write("Salvagebackend voltooid\n")
        return (SimpleNamespace(
            rar_setnaam="megaman2007",
            spotify_recovery_items=1,
            eindstatus="SALVAGED",
            fysiek_aanwezig=1,
            goed=1,
            ffmpeg_fouten=0,
            nul_bytes=0,
        ),)

    def test_stille_workflow_stopt_voor_playlist_en_vraagt_review(self):
        playlist_calls = []

        def search(*args, **kwargs):
            kwargs["progress_callback"](maak_progress(
                "Spotify Search", 1, 1, "Track verwerkt.",
                matched_count=1,
                low_confidence_count=0,
                manual_review_count=0,
                not_found_count=0,
                error_count=0,
            ))
            return SimpleNamespace(
                recovery_set_id=self.set_id,
                total=1,
            )

        workflow = RecoveryGuiWorkflow(
            analyse=self._analyse,
            salvage=self._salvage,
            spotify_search=search,
            playlist_sync=lambda *args, **kwargs: (
                playlist_calls.append(kwargs["archive_set_name"])
                or SimpleNamespace(
                    added=1, already_present=0,
                    playlist_id="playlist-id",
                    playlist_name="Megaman2007",
                )
            ),
            report=lambda *args: self.root / "rapport.txt",
            database_factory=maak_database,
            database_path=self.database_path,
        )
        events = EventRecorder()
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            summary = workflow.run(self.root, events.callbacks())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(playlist_calls, [])
        self.assertEqual(summary["matched"], 1)
        self.assertIsNone(summary["playlist_id"])
        self.assertEqual(summary["recovery_set_id"], self.set_id)
        self.assertTrue(summary["review_required"])
        self.assertIn("Recovery Review", events.started)
        self.assertNotIn("Playlist Sync", events.started)
        self.assertEqual(events.reviews[0]["recovery_set_id"], self.set_id)
        self.assertIn(
            (
                "Playlist Sync",
                "Wacht op een afgeronde Recovery Review; "
                "er is geen playlist aangemaakt.",
            ),
            events.skipped,
        )
        self.assertEqual(events.failed, [])
        self.assertEqual(events.progress[-1].percent, 100)

    def test_spotify_zonder_configuratie_wordt_overgeslagen(self):
        workflow = RecoveryGuiWorkflow(
            analyse=self._analyse,
            salvage=self._salvage,
            spotify_search=lambda *args, **kwargs: (
                (_ for _ in ()).throw(
                    SpotifyConfigurationError("credentials ontbreken")
                )
            ),
            playlist_sync=lambda *args, **kwargs: self.fail(
                "playlist mag niet starten"
            ),
            report=lambda *args: self.root / "rapport.txt",
            database_factory=maak_database,
            database_path=self.database_path,
        )
        events = EventRecorder()
        summary = workflow.run(self.root, events.callbacks())
        self.assertIn(
            ("Spotify Search", "credentials ontbreken"),
            events.skipped,
        )
        self.assertTrue(any(
            stage == "Playlist Sync" for stage, _ in events.skipped
        ))
        self.assertIsNone(summary["playlist_id"])


class RecoverySetNaamTest(unittest.TestCase):
    def test_multipart_rar_wint_van_nzbget_mapnaam(self):
        with tempfile.TemporaryDirectory(
            prefix="4fe20a254f204822ed17e7c3.#3."
        ) as root:
            root = Path(root)
            (root / "Megaman2007.part01.rar").touch()
            (root / "Megaman2007.part02.rar").touch()
            self.assertEqual(
                bepaal_recovery_setnaam(root, root / "geen.db"),
                "Megaman2007",
            )

    def test_oude_rar_en_par2_worden_afgeleid(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "Jaarcollectie.rar").touch()
            (root / "Jaarcollectie.r00").touch()
            self.assertEqual(
                bepaal_recovery_setnaam(root, root / "geen.db"),
                "Jaarcollectie",
            )
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "Megaman2007.vol000+01.par2").touch()
            self.assertEqual(
                bepaal_recovery_setnaam(root, root / "geen.db"),
                "Megaman2007",
            )

    def test_bestaande_databasenaam_en_mapfallback(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "megaman2007.part01.rar").touch()
            database_path = root / "db.sqlite"
            database = maak_database(database_path)
            verkrijg_of_maak_recovery_set(
                database,
                "megaman2007.part01.rar",
                archive_set_name="Megaman2007",
            )
            database.sluit()
            self.assertEqual(
                bepaal_recovery_setnaam(root, database_path),
                "Megaman2007",
            )
        with tempfile.TemporaryDirectory(prefix="GewoneMap.") as root:
            root = Path(root)
            self.assertEqual(
                bepaal_recovery_setnaam(root, root / "geen.db"),
                root.name,
            )


class StilleScannerProgressTest(unittest.TestCase):
    def test_gui_scanner_is_stil_en_levert_structuur(self):
        from scanner import controleer_mp3_bestanden

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            bestand = root / "Artist – Één's Track.mp3"
            bestand.write_bytes(b"data")
            database = maak_database(root / "scan.db")
            progress = []
            stdout = io.StringIO()
            try:
                with (
                    patch(
                        "scanner.controleer_bestand",
                        return_value=(
                            bestand.name, "OK", None, None
                        ),
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    controleer_mp3_bestanden(
                        (bestand,),
                        root,
                        database,
                        progress_callback=progress.append,
                        console_progress=False,
                    )
            finally:
                database.sluit()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(progress[-1].percent, 100)
        self.assertEqual(progress[-1].ok_count, 1)


if __name__ == "__main__":
    unittest.main()
