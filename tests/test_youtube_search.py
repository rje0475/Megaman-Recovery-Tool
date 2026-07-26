import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

from core.youtube.models import YouTubeVideo
from core.youtube.provider import (
    YouTubeApiError, YouTubeConfigurationError, YouTubeSearchProvider,
)
from core.youtube.scoring import normaliseer_zoektekst, score_video
from core.youtube.search import (
    bouw_zoekopdrachten, laad_youtube_kandidaten,
    markeer_geen_youtube_bron, selecteer_youtube_kandidaat,
    zoek_youtube_kandidaten,
)
from database import SQLiteDatabase


class FakeProvider:
    def __init__(self, results=None, error=None):
        self.results = tuple(results or ())
        self.error = error
        self.queries = []

    def search(self, query, limit=10):
        self.queries.append((query, limit))
        if self.error:
            raise self.error
        return self.results


class YouTubeSearchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "youtube.sqlite"
        self.db = SQLiteDatabase(self.path)
        now = "2026-07-26T00:00:00"
        self.db.verbinding.execute(
            "INSERT INTO recovery_sets(archive_name,archive_set_name,created_at,updated_at) VALUES(?,?,?,?)",
            ("Collectie.part01.rar", "Collectie", now, now),
        )
        set_id = self.db.verbinding.execute(
            "SELECT id FROM recovery_sets"
        ).fetchone()[0]
        self.db.verbinding.execute(
            """INSERT INTO recovery_items(
            rar_set_key,verwacht_rel_pad,verwacht_rel_pad_norm,probleem_type,
            probleem_bron,bepaalde_artiest,bepaalde_titel,aangemaakt_op,
            bijgewerkt_op,recovery_set_id,playlist_selected)
            VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
            ("Collectie", "2007/07050090 Delain - Frozen (Radio Edit).mp3",
             "2007/07050090 delain - frozen (radio edit).mp3", "unreadable",
             "salvage", "Delain", "Frozen (Radio Edit)", now, now, set_id),
        )
        self.item_id = self.db.verbinding.execute(
            "SELECT id FROM recovery_items"
        ).fetchone()[0]
        self.db.verbinding.commit()

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def video(self, video_id="one", title="Delain Frozen Radio Edit", **kw):
        return YouTubeVideo(
            video_id, f"https://www.youtube.com/watch?v={video_id}", title,
            kw.get("channel", "Delain - Topic"), kw.get("duration", 240),
            "2007-01-01T00:00:00Z", 1234, "https://img/one.jpg",
        )

    def test_querybouw_ruimt_storing_op_en_behoudt_versie(self):
        queries = bouw_zoekopdrachten(
            "Delain", "Frozen", "Radio Edit",
            "07050090 Delain - Frozen Radio Edit OFFICIAL VIDEO.mp3",
        )
        self.assertEqual(queries[0], "Delain Frozen Radio Edit")
        self.assertNotIn("OFFICIAL VIDEO", queries[2].upper())
        self.assertNotIn("07050090", " ".join(queries))

    def test_normalisatie_verwijdert_noise_niet_relevante_versie(self):
        value = normaliseer_zoektekst("07050090 Délaïn – Frozen Radio Edit [HD].mp3")
        self.assertIn("radio edit", value)
        self.assertNotIn("07050090", value)
        self.assertNotIn("hd", value.split())

    def test_scoring_componenten_en_waarschuwingen(self):
        good = score_video("Delain", "Frozen", "Radio Edit", 240, self.video())
        bad = score_video(
            "Delain", "Frozen", "Radio Edit", 240,
            self.video("bad", "Frozen karaoke sped up live", channel="Uploads", duration=100),
        )
        self.assertGreater(good.confidence, bad.confidence)
        self.assertIn("Karaoke", bad.warnings)
        self.assertIn("Sped up", bad.warnings)
        self.assertIn("Duur wijkt sterk af", bad.warnings)

    def test_zoeken_dedupliceert_beperkt_en_bewaart_componenten(self):
        results = [self.video(str(i)) for i in range(12)] + [self.video("1")]
        provider = FakeProvider(results)
        rows, missing = zoek_youtube_kandidaten(self.db, self.item_id, provider)
        self.assertFalse(missing)
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(limit == 10 for _, limit in provider.queries))
        self.assertGreater(rows[0]["artist_score"], 0)
        self.assertIsInstance(json.loads(rows[0]["warnings_json"]), list)

    def test_selectie_direct_opgeslagen_en_hersteld(self):
        zoek_youtube_kandidaten(self.db, self.item_id, FakeProvider([self.video()]))
        candidate = laad_youtube_kandidaten(self.db, self.item_id)[0]
        selecteer_youtube_kandidaat(self.db, self.item_id, candidate["id"])
        row = self.db.verbinding.execute(
            "SELECT * FROM recovery_items WHERE id=?", (self.item_id,)
        ).fetchone()
        self.assertEqual(row["selected_youtube_video_id"], "one")
        self.assertEqual(row["youtube_review_status"], "SELECTED")
        self.assertEqual(row["preferred_audio_source"], "YOUTUBE")

    def test_geen_geschikte_bron_is_explicit(self):
        markeer_geen_youtube_bron(self.db, self.item_id)
        row = self.db.verbinding.execute(
            "SELECT * FROM recovery_items WHERE id=?", (self.item_id,)
        ).fetchone()
        self.assertEqual(row["youtube_review_status"], "REVIEWED_NONE")
        self.assertIsNone(row["selected_youtube_url"])

    def test_search_again_behoudt_keuze_en_meldt_verdwenen_video(self):
        zoek_youtube_kandidaten(self.db, self.item_id, FakeProvider([self.video()]))
        candidate = laad_youtube_kandidaten(self.db, self.item_id)[0]
        selecteer_youtube_kandidaat(self.db, self.item_id, candidate["id"])
        _, missing = zoek_youtube_kandidaten(
            self.db, self.item_id, FakeProvider([self.video("two")]), True
        )
        self.assertTrue(missing)
        row = self.db.verbinding.execute(
            "SELECT selected_youtube_video_id FROM recovery_items WHERE id=?",
            (self.item_id,),
        ).fetchone()
        self.assertEqual(row[0], "one")

    def test_fout_behoudt_bestaande_keuze(self):
        zoek_youtube_kandidaten(self.db, self.item_id, FakeProvider([self.video()]))
        candidate = laad_youtube_kandidaten(self.db, self.item_id)[0]
        selecteer_youtube_kandidaat(self.db, self.item_id, candidate["id"])
        with self.assertRaisesRegex(RuntimeError, "quota"):
            zoek_youtube_kandidaten(
                self.db, self.item_id, FakeProvider(error=RuntimeError("quota")), True
            )
        row = self.db.verbinding.execute(
            "SELECT selected_youtube_video_id,youtube_search_error FROM recovery_items WHERE id=?",
            (self.item_id,),
        ).fetchone()
        self.assertEqual(row[0], "one")
        self.assertEqual(row[1], "quota")

    def test_lege_resultaten_en_geen_downloadoppervlak(self):
        rows, _ = zoek_youtube_kandidaten(self.db, self.item_id, FakeProvider())
        self.assertEqual(rows, ())
        self.assertFalse(hasattr(YouTubeSearchProvider, "download"))
        self.assertFalse(hasattr(YouTubeSearchProvider, "download_audio"))

    def test_ontbrekende_configuratie(self):
        with self.assertRaises(YouTubeConfigurationError):
            YouTubeSearchProvider.from_environment({})

    def test_provider_normaliseert_api_resultaat(self):
        provider = YouTubeSearchProvider("geheim")
        video = provider.normalize_result({
            "id": "abc", "snippet": {
                "title": "Titel", "channelTitle": "Kanaal",
                "publishedAt": "2007-01-01", "thumbnails": {
                    "medium": {"url": "https://img"}
                },
            },
            "contentDetails": {"duration": "PT3M42S"},
            "statistics": {"viewCount": "123"},
        })
        self.assertEqual(video.duration_seconds, 222)
        self.assertEqual(video.view_count, 123)
        self.assertEqual(video.url, "https://www.youtube.com/watch?v=abc")

    def test_quota_429_wordt_duidelijk_gemeld(self):
        headers = Message()
        headers["Retry-After"] = "12"
        def opener(*_args, **_kwargs):
            raise HTTPError("https://api", 429, "Too Many", headers, None)
        provider = YouTubeSearchProvider("geheim", opener=opener)
        with self.assertRaises(YouTubeApiError) as caught:
            provider.search("Delain Frozen")
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.retry_after, "12")
        self.assertIn("limiet", str(caught.exception))

    def test_migraties_zijn_aanwezig(self):
        recovery_columns = {
            row["name"] for row in self.db.verbinding.execute("PRAGMA table_info(recovery_items)")
        }
        self.assertIn("selected_youtube_candidate_id", recovery_columns)
        self.assertIn("preferred_audio_source", recovery_columns)
        self.assertTrue(self.db.verbinding.execute(
            "SELECT name FROM sqlite_master WHERE name='youtube_candidates'"
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
