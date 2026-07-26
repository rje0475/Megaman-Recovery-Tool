import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mutagen.id3 import ID3

from core.metadata.artwork import ArtworkCache
from core.metadata.errors import FinalizationSkipped
from core.metadata.filename import collision_target, render_filename, safe_component
from core.metadata.finalizer import MetadataFinalizer
from core.metadata.models import MetadataConfig, TrackMetadata
from database import SQLiteDatabase


JPEG = b"\xff\xd8test-cover\xff\xd9"


class FakeResponse:
    def read(self):
        return JPEG


class MetadataFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = SQLiteDatabase(self.root / "test.db")
        now = "2026-01-01T00:00:00"
        self.db.verbinding.execute(
            """INSERT INTO rar_sets(rar_set_key,rar_startbestand,
            listing_volledig,inventaris_bron,actief,bijgewerkt_op)
            VALUES('set','Collection.part01.rar',1,'test',1,?)""", (now,)
        )
        self.db.verbinding.execute(
            """INSERT INTO recovery_items(id,rar_set_key,verwacht_rel_pad,
            verwacht_rel_pad_norm,probleem_type,probleem_bron,bepaalde_artiest,
            bepaalde_titel,bepaald_tracknummer,aangemaakt_op,bijgewerkt_op,
            selected_spotify_artist,selected_spotify_title,selected_spotify_album)
            VALUES(1,'set','2007/07110066 Artist - Song.mp3','x','MISSING','test',
            'ignored','ignored','7',?,?, 'Spotify Artist','Spotify Song','Spotify Album')""",
            (now, now),
        )
        cursor = self.db.verbinding.execute(
            """INSERT INTO spotify_candidates(recovery_item_id,spotify_track_id,
            spotify_url,artist,title,album,duration_ms,total_score,artist_score,
            title_score,version_score,duration_score,search_strategy,search_query,
            score_reason,spotify_uri,album_cover_url,release_date)
            VALUES(1,'track','https://open.spotify.com/track/track','Spotify Artist',
            'Spotify Song','Spotify Album',1000,99,99,99,99,99,'strict','query',
            'test','spotify:track:track','https://images/cover.jpg','2007-01-01')"""
        )
        self.db.verbinding.execute(
            "UPDATE recovery_items SET selected_spotify_candidate_id=? WHERE id=1",
            (cursor.lastrowid,),
        )
        self.db.verbinding.commit()

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def config(self, policy="Rename", filename="{artist} - {title}.mp3"):
        return MetadataConfig(
            output_root=self.root / "Recovered", filename_template=filename,
            folder_template="{year}/Week {week}", collision_policy=policy,
            artwork_cache=self.root / "covers",
        )

    def finalizer(self, policy="Rename", filename="{artist} - {title}.mp3"):
        cache = ArtworkCache(self.root / "covers", opener=lambda *_a, **_k: FakeResponse())
        return MetadataFinalizer(self.db, self.config(policy, filename), artwork_cache=cache)

    def job(self):
        source = self.root / "processed_audio.mp3"
        source.write_bytes(b"audio payload")
        return SimpleNamespace(processed_path=str(source), recovery_item_id=1)

    def test_writes_id3_artwork_and_atomically_finalizes(self):
        job = self.job()
        result = self.finalizer().finalize(job)
        self.assertFalse(Path(job.processed_path).exists())
        self.assertTrue(result.final_path.is_file())
        tags = ID3(result.final_path)
        self.assertEqual(str(tags["TIT2"]), "Spotify Song")
        self.assertEqual(str(tags["TPE1"]), "Spotify Artist")
        self.assertEqual(str(tags["TALB"]), "Spotify Album")
        self.assertTrue(tags.getall("APIC"))
        self.assertIn("Recovered by Megaman Recovery Tool", str(tags.getall("COMM")[0]))
        self.assertFalse(any(p.suffix == ".tmp" for p in result.final_path.parent.iterdir()))

    def test_templates_and_forbidden_characters(self):
        metadata = TrackMetadata("A: Song?", "CON", "Album", "Artist", "03", year="2007", week="11")
        self.assertEqual(render_filename("{track} - {artist} - {title}", metadata), "03 - _CON - A Song.mp3")
        self.assertEqual(safe_component("  A   B. "), "A B")

    def test_collision_rename_overwrite_and_skip(self):
        first = self.finalizer().finalize(self.job()).final_path
        renamed = self.finalizer().finalize(self.job()).final_path
        self.assertNotEqual(first, renamed)
        overwrite = self.finalizer("Overwrite").finalize(self.job()).final_path
        self.assertEqual(overwrite, first)
        with self.assertRaises(FinalizationSkipped):
            self.finalizer("Skip").finalize(self.job())
        self.assertTrue(Path(self.root / "processed_audio.mp3").exists())

    def test_artwork_is_cached_once(self):
        calls = []
        cache = ArtworkCache(self.root / "cache", opener=lambda *_a, **_k: calls.append(1) or FakeResponse())
        cache.get("https://images/cover.jpg")
        cache.get("https://images/cover.jpg")
        self.assertEqual(calls, [1])

    def test_migration_contains_finalization_columns(self):
        columns = {row["name"] for row in self.db.verbinding.execute("PRAGMA table_info(download_queue)")}
        self.assertTrue({"final_path", "filename", "metadata_written", "artwork_written",
                         "finalized_at", "finalization_status"} <= columns)

    def test_complete_finalization_updates_queue_and_recovery_item(self):
        now = "2026-01-01T00:00:00"
        self.db.verbinding.execute(
            """INSERT INTO download_queue(job_id,recovery_item_id,source_type,status,
            queue_position,created_at,updated_at) VALUES('job',1,'YOUTUBE','PROCESSED',1,?,?)""",
            (now, now),
        )
        self.db.verbinding.commit()
        from core.download_queue import DownloadQueueManager, RECOVERED
        result = self.finalizer().finalize(self.job())
        manager = DownloadQueueManager(self.db)
        manager.complete_finalization("job", result)
        self.assertEqual(manager.get("job").status, RECOVERED)
        item = self.db.verbinding.execute("SELECT geplaatst FROM recovery_items WHERE id=1").fetchone()
        self.assertEqual(item["geplaatst"], 1)


if __name__ == "__main__":
    unittest.main()
