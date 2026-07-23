import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import (
    SQLiteDatabase,
    bewaar_provider_resultaat,
    voeg_mp3_toe,
    maak_database,
    verkrijg_provider_resultaat,
    zet_nul_bytes,
)
from spotify import (
    MuziekResultaat,
    SpotifyApiFout,
    schoon_mp3_bestandsnaam,
    schoon_spotify_zoekwaarden,
    voer_spotify_scan_uit,
    zoek_en_bewaar_spotify_nummer,
)


class NepSpotifyClient:
    def __init__(self, resultaten):
        self.resultaten = list(resultaten)
        self.aanroepen = []

    def zoek_nummer(self, artiest, titel):
        self.aanroepen.append((artiest, titel))
        resultaat = self.resultaten.pop(0)

        if isinstance(resultaat, Exception):
            raise resultaat

        return resultaat


def resultaat(zoek_artiest, zoek_titel, gevonden):
    if not gevonden:
        return MuziekResultaat(
            provider="spotify",
            zoek_artiest=zoek_artiest,
            zoek_titel=zoek_titel,
            gevonden=False,
        )

    return MuziekResultaat(
        provider="spotify",
        zoek_artiest=zoek_artiest,
        zoek_titel=zoek_titel,
        gevonden=True,
        track_id="track-123",
        url="https://open.spotify.com/track/track-123",
        artiest="New Order",
        titel="Blue Monday",
        album="Power, Corruption & Lies",
        duur_ms=449000,
    )


class OpschonenTest(unittest.TestCase):
    def test_verwijdert_tracknummers_en_extensie(self):
        gevallen = {
            "01 Blue Monday.mp3": "Blue Monday",
            "01. Blue Monday.MP3": "Blue Monday",
            "01 - Blue Monday.mp3": "Blue Monday",
            "1_Blue Monday.mp3": "Blue Monday",
        }

        for invoer, verwacht in gevallen.items():
            with self.subTest(invoer=invoer):
                self.assertEqual(
                    schoon_mp3_bestandsnaam(invoer),
                    verwacht,
                )

    def test_verwijdert_officiele_video_audio_en_lyrics_termen(self):
        gevallen = {
            "Blue Monday (Official Video).mp3": "Blue Monday",
            "Blue Monday [Official Audio] HQ.mp3": "Blue Monday",
            "Blue Monday (Lyric Video) [HD].mp3": "Blue Monday",
            "Blue Monday - Lyrics.mp3": "Blue Monday",
        }

        for invoer, verwacht in gevallen.items():
            with self.subTest(invoer=invoer):
                self.assertEqual(
                    schoon_mp3_bestandsnaam(invoer),
                    verwacht,
                )

    def test_verwijdert_remastervarianten(self):
        gevallen = {
            "Blue Monday (Remastered).mp3": "Blue Monday",
            "Blue Monday [2011 Remaster].mp3": "Blue Monday",
            "Blue Monday (Remastered 2009).mp3": "Blue Monday",
            "Blue Monday [1999 Digital Remaster Version].mp3": "Blue Monday",
        }

        for invoer, verwacht in gevallen.items():
            with self.subTest(invoer=invoer):
                self.assertEqual(
                    schoon_mp3_bestandsnaam(invoer),
                    verwacht,
                )

    def test_normaliseert_haakjes_underscores_en_spaties(self):
        self.assertEqual(
            schoon_mp3_bestandsnaam(
                " 01 - Blue__Monday [The Mix]  .mp3"
            ),
            "Blue Monday The Mix",
        )

    def test_behoudt_artiest_en_titel(self):
        self.assertEqual(
            schoon_spotify_zoekwaarden(
                "New_Order",
                "01 - Blue_Monday (Official Video).mp3",
            ),
            ("New Order", "Blue Monday"),
        )


class SpotifyFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tijdelijke_map = tempfile.TemporaryDirectory()
        self.root = Path(self.tijdelijke_map.name)
        self.database = maak_database(self.root / "test.sqlite3")
        self.muziek_map = self.root / "muziek"
        self.bestand = self.muziek_map / "nummer.mp3"
        voeg_mp3_toe(
            self.database,
            self.muziek_map,
            self.bestand,
        )

    def tearDown(self):
        self.database.sluit()
        self.tijdelijke_map.cleanup()

    def test_gebruikt_geen_fallback_bij_origineel_resultaat(self):
        client = NepSpotifyClient([
            resultaat("New Order", "Blue Monday", True),
        ])

        gevonden = zoek_en_bewaar_spotify_nummer(
            self.database,
            "nummer.mp3",
            "New Order",
            "Blue Monday",
            client,
        )

        self.assertEqual(
            client.aanroepen,
            [("New Order", "Blue Monday")],
        )
        self.assertEqual(gevonden.zoekmethode, "original")
        opgeslagen = verkrijg_provider_resultaat(
            self.database,
            "nummer.mp3",
            "spotify",
        )
        self.assertEqual(opgeslagen["zoekmethode"], "original")

    def test_gebruikt_opgeschoonde_fallback_na_een_miss(self):
        client = NepSpotifyClient([
            resultaat(
                "New_Order",
                "01 - Blue_Monday (Official Video).mp3",
                False,
            ),
            resultaat("New Order", "Blue Monday", True),
        ])

        gevonden = zoek_en_bewaar_spotify_nummer(
            self.database,
            "nummer.mp3",
            "New_Order",
            "01 - Blue_Monday (Official Video).mp3",
            client,
        )

        self.assertEqual(
            client.aanroepen,
            [
                (
                    "New_Order",
                    "01 - Blue_Monday (Official Video).mp3",
                ),
                ("New Order", "Blue Monday"),
            ],
        )
        self.assertEqual(gevonden.zoekmethode, "cleaned")
        opgeslagen = verkrijg_provider_resultaat(
            self.database,
            "nummer.mp3",
            "spotify",
        )
        self.assertTrue(opgeslagen["gevonden"])
        self.assertEqual(opgeslagen["zoekmethode"], "cleaned")
        self.assertEqual(opgeslagen["track_id"], "track-123")

    def test_slaat_not_found_op_na_twee_misses(self):
        client = NepSpotifyClient([
            resultaat(
                "Onbekend",
                "01 - Onbekend (Official Audio).mp3",
                False,
            ),
            resultaat("Onbekend", "Onbekend", False),
        ])

        gevonden = zoek_en_bewaar_spotify_nummer(
            self.database,
            "nummer.mp3",
            "Onbekend",
            "01 - Onbekend (Official Audio).mp3",
            client,
        )

        self.assertEqual(len(client.aanroepen), 2)
        self.assertEqual(gevonden.zoekmethode, "not_found")
        opgeslagen = verkrijg_provider_resultaat(
            self.database,
            "nummer.mp3",
            "spotify",
        )
        self.assertFalse(opgeslagen["gevonden"])
        self.assertEqual(opgeslagen["zoekmethode"], "not_found")
        self.assertIsNone(opgeslagen["track_id"])


class DatabaseMigratieTest(unittest.TestCase):
    def test_voegt_zoekmethode_toe_aan_bestaande_database(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            databasepad = Path(tijdelijke_map) / "bestaand.sqlite3"
            verbinding = sqlite3.connect(databasepad)
            verbinding.executescript(
                """
                CREATE TABLE mp3_bestanden (
                    relatief_pad TEXT PRIMARY KEY,
                    bestand TEXT NOT NULL,
                    bestaat INTEGER NOT NULL,
                    nul_bytes INTEGER NOT NULL,
                    rar_status TEXT NOT NULL,
                    rar_type TEXT,
                    ffmpeg_status TEXT NOT NULL,
                    ffmpeg_type TEXT,
                    ffmpeg_melding TEXT
                );
                CREATE TABLE provider_resultaten (
                    relatief_pad TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    zoek_artiest TEXT NOT NULL,
                    zoek_titel TEXT NOT NULL,
                    gevonden INTEGER NOT NULL,
                    track_id TEXT,
                    url TEXT,
                    artiest TEXT,
                    titel TEXT,
                    album TEXT,
                    duur_ms INTEGER,
                    PRIMARY KEY (relatief_pad, provider)
                );
                INSERT INTO mp3_bestanden VALUES (
                    'nummer.mp3',
                    'nummer.mp3',
                    1,
                    0,
                    'NIET_GECONTROLEERD',
                    NULL,
                    'OK',
                    NULL,
                    NULL
                );
                INSERT INTO provider_resultaten VALUES (
                    'nummer.mp3',
                    'spotify',
                    'Artiest',
                    'Titel',
                    0,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL
                );
                """
            )
            verbinding.commit()
            verbinding.close()

            database = SQLiteDatabase(databasepad)
            try:
                opgeslagen = verkrijg_provider_resultaat(
                    database,
                    "nummer.mp3",
                    "spotify",
                )
                self.assertEqual(
                    opgeslagen["zoekmethode"],
                    "not_found",
                )
            finally:
                database.sluit()


class SpotifyScanFlowTest(unittest.TestCase):
    def setUp(self):
        self.tijdelijke_map = tempfile.TemporaryDirectory()
        self.root = Path(self.tijdelijke_map.name)
        self.databasepad = self.root / "test.sqlite3"
        self.muziek_map = self.root / "muziek"

    def tearDown(self):
        self.tijdelijke_map.cleanup()

    def test_hervat_en_slaat_nul_byte_over(self):
        database = maak_database(self.databasepad)
        eerder_bestand = self.muziek_map / "Artiest A - Titel A.mp3"
        voeg_mp3_toe(
            database,
            self.muziek_map,
            eerder_bestand,
        )
        bewaar_provider_resultaat(
            database,
            "Artiest A - Titel A.mp3",
            "spotify",
            "Artiest A",
            "Titel A",
            True,
            track_id="eerder-track-id",
            zoekmethode="original",
        )
        database.sluit()

        database = maak_database(self.databasepad)
        nieuw_bestand = self.muziek_map / "Artiest B - Titel B.mp3"
        nul_bestand = self.muziek_map / "Artiest C - Titel C.mp3"

        for bestand in (
            eerder_bestand,
            nieuw_bestand,
            nul_bestand,
        ):
            voeg_mp3_toe(
                database,
                self.muziek_map,
                bestand,
            )

        zet_nul_bytes(
            database,
            self.muziek_map,
            nul_bestand,
        )

        client = NepSpotifyClient([
            resultaat("Artiest B", "Titel B", True),
        ])
        uitvoer = io.StringIO()

        try:
            scan = voer_spotify_scan_uit(
                database,
                client=client,
                uitvoer=uitvoer,
            )

            self.assertEqual(
                client.aanroepen,
                [("Artiest B", "Titel B")],
            )
            self.assertEqual(scan.totaal, 1)
            self.assertEqual(scan.verwerkt, 1)
            self.assertEqual(scan.gevonden, 1)
            self.assertEqual(scan.niet_gevonden, 0)
            self.assertEqual(scan.fouten, 0)
            self.assertEqual(scan.overgeslagen, 1)
            self.assertIn(
                "Spotify: 1/1 | gevonden: 1 | "
                "niet gevonden: 0 | fouten: 0",
                uitvoer.getvalue(),
            )
            self.assertTrue(uitvoer.getvalue().endswith("\n"))
        finally:
            database.sluit()

    def test_ontbrekende_credentials_slaan_stap_over(self):
        database = maak_database(self.databasepad)
        uitvoer = io.StringIO()

        try:
            with patch.dict(os.environ, {}, clear=True):
                scan = voer_spotify_scan_uit(
                    database,
                    uitvoer=uitvoer,
                )

            self.assertTrue(scan.credentials_ontbreken)
            self.assertEqual(scan.verwerkt, 0)
            self.assertIn(
                "Spotify overgeslagen",
                uitvoer.getvalue(),
            )
            self.assertIn(
                "SPOTIFY_CLIENT_ID",
                uitvoer.getvalue(),
            )
            self.assertIn(
                "SPOTIFY_CLIENT_SECRET",
                uitvoer.getvalue(),
            )
        finally:
            database.sluit()

    def test_api_fout_stopt_volgende_nummer_niet(self):
        database = maak_database(self.databasepad)
        eerste = self.muziek_map / "Artiest A - Titel A.mp3"
        tweede = self.muziek_map / "Artiest B - Titel B.mp3"

        for bestand in (eerste, tweede):
            voeg_mp3_toe(
                database,
                self.muziek_map,
                bestand,
            )

        client = NepSpotifyClient([
            SpotifyApiFout("tijdelijke fout"),
            resultaat("Artiest B", "Titel B", True),
        ])

        try:
            scan = voer_spotify_scan_uit(
                database,
                client=client,
                uitvoer=io.StringIO(),
            )

            self.assertEqual(scan.verwerkt, 2)
            self.assertEqual(scan.fouten, 1)
            self.assertEqual(scan.gevonden, 1)
            self.assertIsNone(
                verkrijg_provider_resultaat(
                    database,
                    "Artiest A - Titel A.mp3",
                    "spotify",
                )
            )
            self.assertIsNotNone(
                verkrijg_provider_resultaat(
                    database,
                    "Artiest B - Titel B.mp3",
                    "spotify",
                )
            )
        finally:
            database.sluit()


if __name__ == "__main__":
    unittest.main()
