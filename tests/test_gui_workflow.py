import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.spotify.client import SpotifyConfigurationError
from database import maak_database, verkrijg_of_maak_recovery_set
from gui.workflow import (
    RecoveryGuiWorkflow,
    WorkflowCallbacks,
    WORKFLOW_STAGES,
)


class EventRecorder:
    def __init__(self):
        self.started = []
        self.progress = []
        self.completed = []
        self.skipped = []
        self.failed = []
        self.logs = []

    def callbacks(self):
        return WorkflowCallbacks(
            stage_started=self.started.append,
            stage_progress=lambda *args: self.progress.append(args),
            stage_completed=self.completed.append,
            stage_skipped=lambda *args: self.skipped.append(args),
            stage_failed=lambda *args: self.failed.append(args),
            log_message=self.logs.append,
        )


class GuiWorkflowAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
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
              'set', ?, 'track.mp3', 'track.mp3',
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

    def _salvage_result(self):
        return SimpleNamespace(
            rar_setnaam="Megaman2007",
            spotify_recovery_items=1,
            eindstatus="SALVAGED",
        )

    def test_bestaande_matches_leiden_tot_playlist_sync(self):
        playlist_calls = []
        workflow = RecoveryGuiWorkflow(
            analyse=lambda *args, **kwargs: None,
            salvage=lambda *args, **kwargs: (self._salvage_result(),),
            spotify_search=lambda *args, **kwargs: SimpleNamespace(
                matched=0, low_confidence=0,
                manual_review=0, not_found=0,
            ),
            playlist_sync=lambda *args, **kwargs: (
                playlist_calls.append(kwargs["archive_set_name"])
                or SimpleNamespace(
                    added=1, already_present=0,
                    playlist_id="playlist-id",
                    playlist_name="Megaman2007",
                )
            ),
            report=lambda *args, **kwargs: self.root / "rapport.txt",
            database_factory=maak_database,
            database_path=self.database_path,
        )
        events = EventRecorder()
        summary = workflow.run(self.root, events.callbacks())
        self.assertEqual(playlist_calls, ["Megaman2007"])
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["playlist_id"], "playlist-id")
        self.assertEqual(events.started, list(WORKFLOW_STAGES))
        self.assertEqual(events.completed, list(WORKFLOW_STAGES))
        self.assertEqual(events.failed, [])

    def test_spotify_zonder_configuratie_wordt_netjes_overgeslagen(self):
        workflow = RecoveryGuiWorkflow(
            analyse=lambda *args, **kwargs: None,
            salvage=lambda *args, **kwargs: (self._salvage_result(),),
            spotify_search=lambda *args, **kwargs: (
                (_ for _ in ()).throw(
                    SpotifyConfigurationError("credentials ontbreken")
                )
            ),
            playlist_sync=lambda *args, **kwargs: self.fail(
                "playlist mag niet starten"
            ),
            report=lambda *args, **kwargs: self.root / "rapport.txt",
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


if __name__ == "__main__":
    unittest.main()
