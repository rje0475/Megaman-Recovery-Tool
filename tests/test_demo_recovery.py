import io
import tempfile
import unittest
from pathlib import Path

from tools.create_demo_recovery_test import (
    DEMO_MARKER,
    DemoFout,
    DemoSpotifyClient,
    maak_demo_spotify_client,
    ruim_demo_op,
    voer_demo_uit,
    _vind_programma,
    FFMPEG_KANDIDATEN,
)


class DemoSpotifyMockTest(unittest.TestCase):
    def test_mockmodus_is_alleen_expliciet_actief(self):
        with self.assertRaises(DemoFout):
            maak_demo_spotify_client(
                demo_mock=False,
                omgeving={},
            )
        self.assertIsInstance(
            maak_demo_spotify_client(demo_mock=True, omgeving={}),
            DemoSpotifyClient,
        )

    def test_mock_levert_found_ambiguous_en_not_found_basis(self):
        client = maak_demo_spotify_client(demo_mock=True)
        found = client.zoek_nummers("Demo Artist", "Missing Track")
        ambiguous = client.zoek_nummers("Demo Artist", "Empty Track")
        not_found = client.zoek_nummers("Demo Artist", "Corrupt Track")
        self.assertEqual(found[0].track_id, "demo-found")
        self.assertEqual(ambiguous[0].track_id, "demo-ambiguous")
        self.assertEqual(not_found, [])


class DemoVeiligheidTest(unittest.TestCase):
    def test_cleanup_weigert_map_zonder_marker(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            with self.assertRaises(DemoFout):
                ruim_demo_op(tijdelijke_map)

    def test_cleanup_verwijdert_alleen_gemarkeerde_demo(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            demo = Path(tijdelijke_map) / "demo"
            demo.mkdir()
            (demo / DEMO_MARKER).write_text(
                str(demo.resolve()) + "\n",
                encoding="utf-8",
            )
            verwijderd = ruim_demo_op(demo)
            self.assertEqual(verwijderd, demo.resolve())
            self.assertFalse(demo.exists())


class DemoPraktijkTest(unittest.TestCase):
    @unittest.skipUnless(
        _vind_programma(FFMPEG_KANDIDATEN),
        "FFmpeg is vereist voor de expliciete demo-praktijktest",
    )
    def test_volledige_fixturevariant_eindigt_met_pass(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            uitvoer = io.StringIO()
            demo = voer_demo_uit(
                basis_map=tijdelijke_map,
                uitvoer=uitvoer,
                rar_programma="",
            )
            self.assertIn("PASS", uitvoer.getvalue())
            self.assertIn(
                "gedocumenteerde listingfixture",
                uitvoer.getvalue(),
            )
            self.assertTrue(
                (demo / "spotify_recovery_playlist.json").exists()
            )
            self.assertTrue((demo / "megaman_demo.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
