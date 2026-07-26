import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.spotify import sync_playlist
from core.spotify.client import SpotifyApiError
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

    def _item(self, status, track_id=None, nummer=1, path=None):
        now = "2026-01-01T00:00:00"
        cursor = self.db.verbinding.execute(
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
                path or f"set/track{nummer}.mp3",
                (path or f"set/track{nummer}.mp3").casefold(),
                track_id,
                f"spotify:track:{track_id}" if track_id else None,
                status, now, now,
            ),
        )
        item_id = cursor.lastrowid
        if status == "MATCHED" and track_id:
            kandidaat = self.db.verbinding.execute(
                """
                INSERT INTO spotify_candidates (
                  recovery_item_id, spotify_track_id, spotify_uri,
                  artist, title, total_score, artist_score, title_score,
                  version_score, duration_score, search_strategy,
                  search_query, selected, rejected, score_reason
                ) VALUES (?, ?, ?, 'Artist', 'Track', .99, .99, .99,
                          1, 1, 'TEST', 'query', 1, 0, 'test')
                """,
                (item_id, track_id, f"spotify:track:{track_id}"),
            )
            self.db.verbinding.execute(
                """
                UPDATE recovery_items SET playlist_selected=1,
                  selected_spotify_candidate_id=?, selected_spotify_uri=?,
                  selected_spotify_track_id=?,
                  match_review_status='AUTO_SELECTED'
                WHERE id=?
                """,
                (
                    kandidaat.lastrowid, f"spotify:track:{track_id}",
                    track_id, item_id,
                ),
            )
        self.db.verbinding.commit()
        return item_id

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
        self.assertIn("Nieuw toegevoegd: 1", log.getvalue())

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

    def test_playlistmanager_haalt_automatisch_gebruikerstoken_op(self):
        fake_client = FakePlaylistClient()
        with (
            patch(
                "core.spotify.playlist.verkrijg_geldig_gebruikerstoken",
                return_value="automatisch-token",
            ) as token_ophalen,
            patch(
                "core.spotify.playlist.SpotifyClient.from_environment",
                return_value=fake_client,
            ) as client_maken,
        ):
            sync_playlist(
                self.db, recovery_set_id=self.set_id,
                uitvoer=io.StringIO(),
            )
        token_ophalen.assert_called_once_with()
        client_maken.assert_called_once_with(
            access_token="automatisch-token"
        )

    def test_alleen_aangevinkte_opgeslagen_keuzes_in_juiste_volgorde(self):
        laat = self._item(
            "MATCHED", "laat", 1,
            "2007/07100020 Artist - Laat.mp3",
        )
        vroeg = self._item(
            "MATCHED", "vroeg", 2,
            "2007/07090010 Artist - Vroeg.mp3",
        )
        uit = self._item(
            "MATCHED", "uit", 3,
            "2007/07080001 Artist - Uit.mp3",
        )
        self.db.verbinding.execute(
            "UPDATE recovery_items SET playlist_selected=0 WHERE id=?",
            (uit,),
        )
        self.db.verbinding.commit()
        client = FakePlaylistClient()
        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual(
            client.added[0][1],
            ("spotify:track:vroeg", "spotify:track:laat"),
        )
        self.assertEqual(summary.unique_selected, 2)
        self.assertNotIn("spotify:track:uit", client.added[0][1])
        self.assertIsNotNone(vroeg)
        self.assertIsNotNone(laat)

    def test_oude_spotify_uri_zonder_opgeslagen_keuze_wordt_genegeerd(self):
        item_id = self._item("LOW_CONFIDENCE", "oud", 1)
        self.db.verbinding.execute(
            """
            UPDATE recovery_items SET playlist_selected=1,
              spotify_uri='spotify:track:oud', spotify_track_id='oud'
            WHERE id=?
            """,
            (item_id,),
        )
        self.db.verbinding.commit()
        client = FakePlaylistClient()
        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual(summary.unique_selected, 0)
        self.assertEqual(summary.unmatched_selected, 1)
        self.assertEqual(client.added, [])

    def test_tweede_run_en_later_nieuw_item(self):
        eerste = self._item("MATCHED", "een", 1)

        class StatefulClient(FakePlaylistClient):
            def __init__(self):
                super().__init__(stored_playlist={
                    "id": "vast", "name": "Megaman2007"
                })
                self.current = set()

            def get_playlist_track_ids(self, playlist_id):
                return frozenset(self.current)

            def add_playlist_items(self, playlist_id, uris):
                super().add_playlist_items(playlist_id, uris)
                self.current.update(uri.rsplit(":", 1)[-1] for uri in uris)

        client = StatefulClient()
        een = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        twee = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual((een.added, twee.added), (1, 0))
        self._item("MATCHED", "twee", 2)
        drie = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual(drie.added, 1)
        self.assertEqual(len(client.added), 2)
        self.assertIsNotNone(eerste)

    def test_401_vernieuwt_token_en_probeert_eenmaal_opnieuw(self):
        self._item("MATCHED", "een", 1)

        class Unauthorized(FakePlaylistClient):
            def list_current_user_playlists(self):
                raise SpotifyApiError("401", status_code=401)

        goed = FakePlaylistClient()
        clients = iter((Unauthorized(), goed))
        tokens = []

        def token_provider(force_refresh=False):
            tokens.append(force_refresh)
            return "nieuw" if force_refresh else "oud"

        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            token_provider=token_provider,
            client_factory=lambda **kwargs: next(clients),
            uitvoer=io.StringIO(),
        )
        self.assertEqual(tokens, [False, True])
        self.assertEqual(summary.added, 1)

    def test_429_retry_after_wordt_gerespecteerd(self):
        self._item("MATCHED", "een", 1)

        class RateLimited(FakePlaylistClient):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def list_current_user_playlists(self):
                self.calls += 1
                if self.calls == 1:
                    raise SpotifyApiError(
                        "429", status_code=429, retry_after=2
                    )
                return ()

        sleeps = []
        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=RateLimited(), sleep=sleeps.append,
            uitvoer=io.StringIO(),
        )
        self.assertEqual(sleeps, [2.0])
        self.assertEqual(summary.sync_status, "SUCCESS")

    def test_gedeeltelijke_batchfout_wordt_bewaard(self):
        for nummer in range(101):
            self._item("MATCHED", f"id-{nummer}", nummer)

        class PartialClient(FakePlaylistClient):
            def add_playlist_items(self, playlist_id, uris):
                if self.added:
                    raise SpotifyApiError("netwerkfout")
                super().add_playlist_items(playlist_id, uris)

        client = PartialClient()
        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual(summary.sync_status, "PARTIAL")
        self.assertEqual(summary.added, 100)
        self.assertIn("netwerkfout", summary.last_error)
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_sets WHERE id=?", (self.set_id,)
        ).fetchone()
        self.assertEqual(rij["spotify_playlist_sync_status"], "PARTIAL")
        self.assertIn("netwerkfout", rij["spotify_playlist_last_error"])

    def test_playlistmetadata_en_echte_url_worden_bewaard(self):
        self._item("MATCHED", "een", 1)
        summary = sync_playlist(
            self.db, recovery_set_id=self.set_id,
            playlist_name="Herstel 2007", client=FakePlaylistClient(),
            uitvoer=io.StringIO(),
        )
        self.assertEqual(
            summary.playlist_url,
            "https://open.spotify.com/playlist/nieuw-id",
        )
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_sets WHERE id=?", (self.set_id,)
        ).fetchone()
        self.assertEqual(rij["spotify_playlist_name"], "Herstel 2007")
        self.assertEqual(rij["spotify_playlist_sync_status"], "SUCCESS")
        self.assertEqual(rij["spotify_playlist_track_count"], 1)
        self.assertIsNotNone(rij["spotify_playlist_synced_at"])

    def test_403_404_en_netwerkfout_worden_bewaard_zonder_selectieverlies(self):
        item_id = self._item("MATCHED", "een", 1)

        class Broken(FakePlaylistClient):
            def __init__(self, error):
                super().__init__()
                self.error = error

            def list_current_user_playlists(self):
                raise self.error

        gevallen = (
            SpotifyApiError("verboden", status_code=403),
            SpotifyApiError("ontbreekt", status_code=404),
            SpotifyApiError("netwerkfout"),
        )
        for error in gevallen:
            with self.subTest(error=str(error)):
                with self.assertRaises(SpotifyPlaylistError):
                    sync_playlist(
                        self.db, recovery_set_id=self.set_id,
                        client=Broken(error), uitvoer=io.StringIO(),
                    )
                set_rij = self.db.verbinding.execute(
                    "SELECT * FROM recovery_sets WHERE id=?", (self.set_id,)
                ).fetchone()
                item = self.db.verbinding.execute(
                    "SELECT * FROM recovery_items WHERE id=?", (item_id,)
                ).fetchone()
                self.assertEqual(
                    set_rij["spotify_playlist_sync_status"], "FAILED"
                )
                self.assertEqual(item["playlist_selected"], 1)
                self.assertIsNotNone(item["selected_spotify_candidate_id"])


if __name__ == "__main__":
    unittest.main()
