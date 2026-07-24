import io
import json
import tempfile
import unittest
from pathlib import Path

from core.spotify.models import (
    LOW_CONFIDENCE,
    MANUAL_REVIEW,
    MATCHED,
    NOT_FOUND,
    SpotifyTrack,
)
from core.spotify.scoring import normaliseer_tekst, score_track
from core.spotify.search import (
    voer_spotify_search_uit,
    zoek_beste_match,
)
from database import (
    leid_archive_set_name_af,
    maak_database,
    verkrijg_of_maak_recovery_set,
)


class FakeSpotifyClient:
    def __init__(self, resultaten=None):
        self.resultaten = resultaten or {}
        self.queries = []

    def search_tracks(self, query, limit=20):
        self.queries.append((query, limit))
        return tuple(self.resultaten.get(query, ()))


def track(
    track_id, artist, title, duration=180000, popularity=50
):
    return SpotifyTrack(
        track_id, f"spotify:track:{track_id}",
        f"https://open.spotify.com/track/{track_id}", "Album",
        (artist,), title, duration, popularity,
    )


class SpotifyScoringTest(unittest.TestCase):
    def test_normalisatie_en_duur_beinvloeden_score(self):
        self.assertEqual(
            normaliseer_tekst("Beyoncé feat. Jay-Z"),
            "beyonce feat jay z",
        )
        exact = score_track(
            "Artist", "Song", 180000,
            track("exact", "Artist", "Song", 180000),
        )
        afwijkend = score_track(
            "Artist", "Song", 180000,
            track("ander", "Other", "Different", 240000),
        )
        self.assertGreater(exact, afwijkend)

    def test_drie_strategieen_en_hoogste_score_wordt_gekozen(self):
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("laag", "Other", "Song"),
            ),
            "Artist Song": (track("beste", "Artist", "Song"),),
            "Song": (track("midden", "Artist", "Song Live"),),
        })
        match = zoek_beste_match(client, "Artist", "Song", 180000)
        self.assertEqual(
            [query for query, _ in client.queries],
            [
                'artist:"Artist" track:"Song"',
                "Artist Song",
                "Song",
            ],
        )
        self.assertEqual(match.track.track_id, "beste")
        self.assertEqual(match.status, MATCHED)
        self.assertEqual(match.search_method, "ARTIST_TITLE")


class SpotifySearchEngineDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = maak_database(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def _item(self, artist, title, path="set/song.mp3"):
        now = "2026-01-01T00:00:00"
        cursor = self.db.verbinding.execute(
            """
            INSERT INTO recovery_items (
              rar_set_key, verwacht_rel_pad, verwacht_rel_pad_norm,
              probleem_type, probleem_bron, feit_ontbreekt,
              spotify_verwerkt, download_verwerkt, geplaatst,
              bepaalde_artiest, bepaalde_titel, aangemaakt_op, bijgewerkt_op
            ) VALUES ('set', ?, ?, 'ontbreekt', 'test', 1, 0, 0, 0,
                      ?, ?, ?, ?)
            """,
            (path, path.casefold(), artist, title, now, now),
        )
        self.db.verbinding.commit()
        return cursor.lastrowid

    def test_recovery_set_wordt_uit_hoofdarchive_afgeleid(self):
        self.assertEqual(
            leid_archive_set_name_af("Andere Naam.part001.rar"),
            "Andere Naam",
        )
        set_id = verkrijg_of_maak_recovery_set(
            self.db, "Andere Naam.part001.rar"
        )
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_sets WHERE id=?", (set_id,)
        ).fetchone()
        self.assertEqual(rij["archive_name"], "Andere Naam.part001.rar")
        self.assertEqual(rij["archive_set_name"], "Andere Naam")

    def test_match_wordt_volledig_op_recovery_item_opgeslagen(self):
        item_id = self._item("Artist", "Song")
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("id-1", "Artist", "Song", popularity=88),
            )
        })
        log = io.StringIO()
        summary = voer_spotify_search_uit(self.db, client, log)
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_items WHERE id=?", (item_id,)
        ).fetchone()
        self.assertEqual(summary.matched, 1)
        self.assertEqual(rij["spotify_track_id"], "id-1")
        self.assertEqual(rij["spotify_uri"], "spotify:track:id-1")
        self.assertEqual(rij["spotify_status"], MATCHED)
        self.assertEqual(rij["spotify_popularity"], 88)
        self.assertEqual(json.loads(rij["spotify_artists"]), ["Artist"])
        self.assertGreater(rij["spotify_confidence"], .85)
        self.assertIn("Confidence:", log.getvalue())

    def test_not_found_low_confidence_en_manual_review_worden_bewaard(self):
        not_found = self._item("Nobody", "Missing", "set/missing.mp3")
        low = self._item("Artist", "Song", "set/low.mp3")
        manual = self._item(None, "Onbekend", "set/manual.mp3")
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("weak", "Different", "Unrelated"),
            )
        })
        summary = voer_spotify_search_uit(
            self.db, client, io.StringIO()
        )
        statuses = {
            rij["id"]: rij["spotify_status"]
            for rij in self.db.verbinding.execute(
                "SELECT id, spotify_status FROM recovery_items"
            )
        }
        self.assertEqual(statuses[not_found], NOT_FOUND)
        self.assertEqual(statuses[low], LOW_CONFIDENCE)
        self.assertEqual(statuses[manual], MANUAL_REVIEW)
        self.assertEqual(
            (summary.not_found, summary.low_confidence, summary.manual_review),
            (1, 1, 1),
        )

    def test_bestaande_handmatige_status_wordt_niet_overschreven(self):
        item_id = self._item("Artist", "Song")
        self.db.verbinding.execute(
            """
            INSERT INTO spotify_smart_results (
              recovery_item_id, local_path, status, checked_at, reason
            ) VALUES (?, 'set/song.mp3', 'MANUAL', 'nu', 'handmatig')
            """,
            (item_id,),
        )
        self.db.verbinding.commit()
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("id-1", "Artist", "Song"),
            )
        })
        summary = voer_spotify_search_uit(
            self.db, client, io.StringIO()
        )
        rij = self.db.verbinding.execute(
            "SELECT spotify_status FROM recovery_items WHERE id=?",
            (item_id,),
        ).fetchone()
        self.assertEqual(summary.skipped_manual, 1)
        self.assertEqual(client.queries, [])
        self.assertIsNone(rij["spotify_status"])


if __name__ == "__main__":
    unittest.main()
