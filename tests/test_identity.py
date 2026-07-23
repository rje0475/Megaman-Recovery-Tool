import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import (
    SQLiteDatabase,
    bewaar_provider_resultaat,
    bewaar_rar_set,
    maak_database,
    vergelijk_rar_inventory,
    vervang_rar_inventory_items,
    voeg_mp3_toe,
    verkrijg_recovery_items,
    zet_ffmpeg_status,
)
from identity import (
    Identiteit,
    bepaal_recovery_identiteiten,
    parseer_verwacht_pad,
    schoon_technische_ruis,
)
from paden import (
    normaliseer_relatief_pad,
    normaliseer_relatief_pad_sleutel,
)
from recovery import genereer_recovery_items


class PadIdentiteitTest(unittest.TestCase):
    def test_ontbrekende_mp3_met_artiest_en_titel(self):
        resultaat = parseer_verwacht_pad(
            r"Album\01 - Artiest - Titel.mp3"
        )
        self.assertEqual(resultaat.artiest, "Artiest")
        self.assertEqual(resultaat.titel, "Titel")
        self.assertEqual(resultaat.tracknummer, "01")

    def test_underscorevariant(self):
        resultaat = parseer_verwacht_pad(r"Artiest_-_Titel.mp3")
        self.assertEqual(
            (resultaat.artiest, resultaat.titel),
            ("Artiest", "Titel"),
        )

    def test_titel_zonder_artiest(self):
        resultaat = parseer_verwacht_pad(r"Album\01 Titel.mp3")
        self.assertIsNone(resultaat.artiest)
        self.assertEqual(resultaat.titel, "Titel")
        self.assertLess(resultaat.betrouwbaarheid, 0.5)

    def test_technische_ruis_wordt_verwijderd(self):
        resultaat = parseer_verwacht_pad(
            r"01 - Artiest - Titel (Official Video) "
            r"[2011 Remastered] 320kbps HD Lyrics.mp3"
        )
        self.assertEqual(resultaat.artiest, "Artiest")
        self.assertEqual(resultaat.titel, "Titel")

    def test_betekenisvolle_haakjes_blijven_staan(self):
        self.assertEqual(
            schoon_technische_ruis("Titel (Live at Wembley)"),
            "Titel (Live at Wembley)",
        )

    def test_mapnaam_als_hint(self):
        resultaat = parseer_verwacht_pad(
            r"Artiest\Album\01 Titel.mp3"
        )
        self.assertEqual(resultaat.artiest, "Artiest")
        self.assertEqual(resultaat.album, "Album")
        self.assertEqual(resultaat.titel, "Titel")

    def test_onherkenbaar_bestand_verzint_niets(self):
        resultaat = parseer_verwacht_pad(r"01.mp3")
        self.assertIsNone(resultaat.artiest)
        self.assertIsNone(resultaat.titel)


class RecoveryIdentiteitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mp3_map = self.root / "mp3"
        self.mp3_map.mkdir()
        self.database = maak_database(self.root / "test.sqlite3")

    def tearDown(self):
        self.database.sluit()
        self.temp.cleanup()

    def _maak_recovery(
        self,
        relatief_pad,
        aanwezig=False,
        inhoud=b"not-an-mp3",
    ):
        pad = normaliseer_relatief_pad(relatief_pad)
        bestandsnaam = pad.rsplit("\\", 1)[-1]
        mapnaam = pad.rsplit("\\", 1)[0] if "\\" in pad else ""
        bewaar_rar_set(
            self.database,
            "set-a",
            self.root / "set-a.part01.rar",
            True,
        )
        vervang_rar_inventory_items(
            self.database,
            "set-a",
            self.root / "set-a.part01.rar",
            [{
                "verwacht_rel_pad": pad,
                "verwacht_rel_pad_norm":
                    normaliseer_relatief_pad_sleutel(pad),
                "verwachte_map": mapnaam,
                "verwachte_bestandsnaam": bestandsnaam,
                "verwachte_grootte": len(inhoud),
                "verwachte_crc32": None,
                "verwachte_modified": None,
            }],
        )
        bestand = None
        if aanwezig:
            bestand = self.mp3_map.joinpath(*Path(relatief_pad).parts)
            bestand.parent.mkdir(parents=True, exist_ok=True)
            bestand.write_bytes(inhoud)
            voeg_mp3_toe(self.database, self.mp3_map, bestand)
            zet_ffmpeg_status(
                self.database,
                str(bestand.relative_to(self.mp3_map)),
                "ERROR",
                "Decode error",
                "test",
            )
        vergelijk_rar_inventory(self.database)
        genereer_recovery_items(self.database, uitvoer=io.StringIO())
        return bestand

    def _bepaal(self, lezer=lambda bestand: None):
        bepaal_recovery_identiteiten(
            self.database,
            metadata_lezer=lezer,
            uitvoer=io.StringIO(),
        )
        return verkrijg_recovery_items(self.database)[0]

    def test_volledige_mp3_metadata(self):
        self._maak_recovery("map/bestand.mp3", aanwezig=True)

        item = self._bepaal(
            lambda bestand: Identiteit(
                artiest="Tagartiest",
                titel="Tagtitel",
                album="Tagalbum",
                tracknummer="7",
                bron="mp3_metadata",
                betrouwbaarheid=0.95,
            )
        )

        self.assertEqual(item["bepaalde_artiest"], "Tagartiest")
        self.assertEqual(item["bepaalde_titel"], "Tagtitel")
        self.assertEqual(item["bepaald_album"], "Tagalbum")
        self.assertEqual(item["bepaald_tracknummer"], "7")
        self.assertEqual(item["identiteit_bron"], "mp3_metadata")

    def test_bestaand_betrouwbaar_spotify_resultaat(self):
        bestand = self._maak_recovery(
            "map/Onbekend.mp3",
            aanwezig=True,
        )
        relatief = str(bestand.relative_to(self.mp3_map))
        bewaar_provider_resultaat(
            self.database,
            relatief,
            "spotify",
            "zoek",
            "zoek",
            True,
            track_id="track-1",
            artiest="Spotify-artiest",
            titel="Spotify-titel",
            album="Spotify-album",
            zoekmethode="original",
        )

        item = self._bepaal()
        self.assertEqual(item["bepaalde_artiest"], "Spotify-artiest")
        self.assertEqual(item["bepaalde_titel"], "Spotify-titel")
        self.assertEqual(item["identiteit_bron"], "spotify_bestaand")

    def test_betrouwbaarheid_wordt_niet_verlaagd(self):
        self._maak_recovery("01 Titel.mp3")
        item = verkrijg_recovery_items(self.database)[0]
        self.database.verbinding.execute(
            """
            UPDATE recovery_items
            SET bepaalde_artiest = 'Behouden',
                bepaalde_titel = 'Betrouwbaar',
                identiteit_bron = 'eerder_bepaald',
                identiteit_betrouwbaarheid = 0.9
            WHERE id = ?
            """,
            (item["id"],),
        )
        self.database.verbinding.commit()

        item = self._bepaal()
        self.assertEqual(item["bepaalde_artiest"], "Behouden")
        self.assertEqual(item["identiteit_betrouwbaarheid"], 0.9)

    def test_incrementele_tweede_run_schrijft_niet_opnieuw(self):
        self._maak_recovery("Artiest - Titel.mp3")
        eerste = self._bepaal()
        eerste_tijd = eerste["identiteit_bepaald_op"]
        eerste_handtekening = eerste["identiteit_bron_handtekening"]

        tweede = self._bepaal()
        self.assertEqual(tweede["identiteit_bepaald_op"], eerste_tijd)
        self.assertEqual(
            tweede["identiteit_bron_handtekening"],
            eerste_handtekening,
        )


class IdentiteitMigratieTest(unittest.TestCase):
    def test_bestaande_database_wordt_automatisch_gemigreerd(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            pad = Path(tijdelijke_map) / "oud.sqlite3"
            verbinding = sqlite3.connect(pad)
            verbinding.execute(
                """
                CREATE TABLE recovery_items (
                    id INTEGER PRIMARY KEY,
                    rar_set_key TEXT NOT NULL,
                    verwacht_rel_pad TEXT NOT NULL,
                    verwacht_rel_pad_norm TEXT NOT NULL,
                    probleem_type TEXT NOT NULL,
                    probleem_bron TEXT NOT NULL,
                    verwachte_grootte INTEGER,
                    verwachte_crc32 TEXT,
                    mp3_id INTEGER,
                    inventaris_id INTEGER,
                    ffmpeg_fout TEXT,
                    rar_fout TEXT,
                    feit_ontbreekt INTEGER NOT NULL DEFAULT 0,
                    feit_rar_crc INTEGER NOT NULL DEFAULT 0,
                    feit_corrupt INTEGER NOT NULL DEFAULT 0,
                    feit_nul_bytes INTEGER NOT NULL DEFAULT 0,
                    feit_grootte_afwijking INTEGER NOT NULL DEFAULT 0,
                    spotify_verwerkt INTEGER NOT NULL DEFAULT 0,
                    download_verwerkt INTEGER NOT NULL DEFAULT 0,
                    geplaatst INTEGER NOT NULL DEFAULT 0,
                    aangemaakt_op TEXT NOT NULL,
                    bijgewerkt_op TEXT NOT NULL,
                    UNIQUE (rar_set_key, verwacht_rel_pad_norm)
                )
                """
            )
            verbinding.commit()
            verbinding.close()

            database = SQLiteDatabase(pad)
            kolommen = {
                rij["name"]
                for rij in database.verbinding.execute(
                    "PRAGMA table_info(recovery_items)"
                )
            }
            database.sluit()

        self.assertTrue({
            "bepaalde_artiest",
            "bepaalde_titel",
            "bepaald_album",
            "bepaald_tracknummer",
            "identiteit_bron",
            "identiteit_betrouwbaarheid",
            "identiteit_bepaald_op",
        }.issubset(kolommen))


if __name__ == "__main__":
    unittest.main()
