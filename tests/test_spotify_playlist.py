import io
import tempfile
import unittest
from pathlib import Path

from core.spotify import sync_playlist
from core.spotify.playlist import SpotifyPlaylistError
from database import maak_database, verkrijg_of_maak_recovery_set


class FakePlaylistClient:
    def __init__(
        self, playlists=(), existing_ids=(), stored_playlist=None
    ):
        self.playlists = tuple(playlists)
        self.existing_ids = frozenset(existing_ids)
        self.stored_playlist = stored_playlist
        self.created = []
        self.added = []
        self.get_calls = []

    def get_playlist(self, playlist_id):
        self.get_calls.append(playlist_id)
        if self.stored_playlist:
            return self.stored_playlist
        return None

    def list_current_user_playlists(self):
        return self.playlists

    def create_playlist(self, name, description, public=False):
        self.created.append((name, description, public))
        return {"id": "nieuw-id", "name": name}

    def get_playlist_track_ids(self, playlist_id):
        return self.existing_ids

    def add_playlist_items(self, playlist_id, uris):
        self.added.append((playlist_id, tuple(uris)))


class SpotifyPlaylistManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = maak_database(Path(self.temp.name) / "playlist.db")
        self.set_id = verkrijg_of_maak_recovery_set(
            self.db, "Megaman2007.part01.rar"
        )

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def _item(self, status, track_id=None, nummer=1):
        now = "2026-01-01T00:00:00"
        self.db.verbinding.execute(
            """
            INSERT INTO recovery_items (
              rar_set_key, recovery_set_id,
              verwacht_rel_pad, verwacht_rel_pad_norm,
              probleem_type, probleem_bron, feit_ontbreekt,
              spotify_verwerkt, download_verwerkt, geplaatst,
              bepaalde_artiest, bepaalde_titel,
              spotify_track_id, spotify_uri, spotify_status,
              aangemaakt_op, bijgewerkt_op
            ) VALUES (
              'set', ?, ?, ?, 'corrupt', 'salvage', 0,
              0, 0, 0, 'Artist', 'Track', ?, ?, ?, ?, ?
            )
            """,
            (
                self.set_id,
                f"set/track{nummer}.mp3",
                f"set/track{nummer}.mp3",
                track_id,
                f"spotify:track:{track_id}" if track_id else None,
                status, now, now,
            ),
        )
        self.db.verbinding.commit()

    def test_opgeslagen_playlist_die_bestaat_wordt_hergebruikt(self):
        self.db.verbinding.execute(
            """
            UPDATE recovery_sets
            SET spotify_playlist_id='bestaand-id',
                spotify_playlist_name='Megaman2007'
            WHERE id=?
            """,
            (self.set_id,),
        )
        self.db.verbinding.commit()
        client = FakePlaylistClient(
            stored_playlist={
                "id": "bestaand-id", "name": "Megaman2007"
            }
        )
        summary = sync_playlist(
            self.db, archive_set_name="Megaman2007",
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual(summary.playlist_id, "bestaand-id")
        self.assertFalse(summary.created)
        self.assertEqual(client.created, [])

    def test_playlist_met_exacte_naam_wordt_gevonden(self):
        client = FakePlaylistClient(playlists=(
            {"id": "ander", "name": "Andere"},
            {"id": "gevonden", "name": "Megaman2007"},
        ))
        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual(summary.playlist_id, "gevonden")
        self.assertFalse(summary.created)
        self.assertEqual(client.created, [])

    def test_playlist_wordt_prive_aangemaakt_en_id_opgeslagen(self):
        client = FakePlaylistClient()
        summary = sync_playlist(
            self.db, archive_set_name="Megaman2007",
            client=client, uitvoer=io.StringIO(),
        )
        self.assertTrue(summary.created)
        self.assertEqual(
            client.created[0],
            (
                "Megaman2007",
                "Recovered tracks from Megaman Recovery Tool",
                False,
            ),
        )
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_sets WHERE id=?", (self.set_id,)
        ).fetchone()
        self.assertEqual(rij["spotify_playlist_id"], "nieuw-id")
        self.assertEqual(rij["spotify_playlist_name"], "Megaman2007")

    def test_alleen_unieke_nog_niet_aanwezige_matched_tracks(self):
        self._item("MATCHED", "een", 1)
        self._item("MATCHED", "een", 2)
        self._item("MATCHED", "twee", 3)
        self._item("LOW_CONFIDENCE", "laag", 4)
        self._item("NOT_FOUND", None, 5)
        self._item("MANUAL_REVIEW", "handmatig", 6)
        client = FakePlaylistClient(existing_ids=("een",))
        log = io.StringIO()
        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=log,
        )
        self.assertEqual(summary.matched_total, 2)
        self.assertEqual(summary.added, 1)
        self.assertEqual(summary.already_present, 1)
        self.assertEqual(
            client.added, [("nieuw-id", ("spotify:track:twee",))]
        )
        self.assertEqual(summary.skipped_low_confidence, 1)
        self.assertEqual(summary.skipped_not_found, 1)
        self.assertEqual(summary.skipped_manual_review, 1)
        self.assertIn("Nieuwe tracks toegevoegd:\n1", log.getvalue())

    def test_lege_recovery_set_en_set_zonder_matched_zijn_geldig(self):
        leeg = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=FakePlaylistClient(), uitvoer=io.StringIO(),
        )
        self.assertEqual((leeg.matched_total, leeg.added), (0, 0))

        self._item("LOW_CONFIDENCE", "laag", 1)
        zonder_match = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=FakePlaylistClient(), uitvoer=io.StringIO(),
        )
        self.assertEqual(
            (zonder_match.matched_total, zonder_match.added), (0, 0)
        )

    def test_recovery_set_moet_explicit_worden_geselecteerd(self):
        with self.assertRaisesRegex(
            SpotifyPlaylistError, "recovery_set_id"
        ):
            sync_playlist(
                self.db, client=FakePlaylistClient(),
                uitvoer=io.StringIO(),
            )


if __name__ == "__main__":
    unittest.main()
