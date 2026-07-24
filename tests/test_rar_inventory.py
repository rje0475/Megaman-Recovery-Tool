import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import SQLiteDatabase
from database import voeg_mp3_toe
from database import maak_database
from paden import normaliseer_relatief_pad
from paden import normaliseer_relatief_pad_sleutel
from rar_inventory import RarListingResultaat
from rar_inventory import groepeer_rar_sets
from rar import zoek_part01_bestanden
from rar_inventory import parseer_7zip_listing
from rar_inventory import voer_rar_inventory_uit


TECHNISCHE_LISTING = """
Listing archive: voorbeeld.part01.rar

----------
Path = Muziek/Artiest/Nummer.MP3
Size = 12345
Packed Size = 12000
Modified = 2024-01-02 03:04:05
Attributes = A
CRC = A1B2C3D4

Path = Muziek/hoes.jpg
Size = 500
Modified = 2024-01-02 03:04:06
CRC = 11223344
"""


def inventaris_item(pad, grootte):
    genormaliseerd = normaliseer_relatief_pad(pad)
    windows_pad = genormaliseerd.rsplit("\\", 1)
    return {
        "verwacht_rel_pad": genormaliseerd,
        "verwacht_rel_pad_norm":
            normaliseer_relatief_pad_sleutel(genormaliseerd),
        "verwachte_map": windows_pad[0] if len(windows_pad) == 2 else "",
        "verwachte_bestandsnaam": windows_pad[-1],
        "verwachte_grootte": grootte,
        "verwachte_crc32": "A1B2C3D4",
        "verwachte_modified": "2024-01-02 03:04:05",
    }


class PadNormalisatieTest(unittest.TestCase):
    def test_normaliseert_scheidingstekens_en_hoofdletters(self):
        self.assertEqual(
            normaliseer_relatief_pad("Artiest/Album/01 Nummer.MP3"),
            r"Artiest\Album\01 Nummer.MP3",
        )
        self.assertEqual(
            normaliseer_relatief_pad_sleutel(
                "Artiest/Album/01 Nummer.MP3"
            ),
            normaliseer_relatief_pad_sleutel(
                r"artiest\album\01 nummer.mp3"
            ),
        )


class ListingParserTest(unittest.TestCase):
    def test_parseert_mp3_uit_technische_7zip_listing(self):
        items = parseer_7zip_listing(TECHNISCHE_LISTING)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["verwacht_rel_pad"],
            r"Muziek\Artiest\Nummer.MP3",
        )
        self.assertEqual(
            items[0]["verwachte_bestandsnaam"],
            "Nummer.MP3",
        )
        self.assertEqual(items[0]["verwachte_grootte"], 12345)
        self.assertEqual(items[0]["verwachte_crc32"], "A1B2C3D4")
        self.assertEqual(
            items[0]["verwachte_modified"],
            "2024-01-02 03:04:05",
        )


class RarGroeperingTest(unittest.TestCase):
    def test_analyse_vindt_eerste_volume_met_variabele_partbreedte(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            for naam in (
                "Collectie.part1.rar",
                "Andere Naam.part001.RAR",
                "Collectie.part2.rar",
                "backup.part01.old",
            ):
                (root / naam).write_bytes(b"")
            self.assertEqual(
                [pad.name for pad in zoek_part01_bestanden(root)],
                ["Andere Naam.part001.RAR", "Collectie.part1.rar"],
            )

    def test_groepeert_multipart_sets_vanaf_part01(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            submap = root / "collectie"
            submap.mkdir()

            for naam in (
                "album.part01.rar",
                "album.part02.rar",
                "album.part03.rar",
                "zonder-start.part02.rar",
            ):
                (submap / naam).write_bytes(b"")

            rar_sets = groepeer_rar_sets(root)

            self.assertEqual(len(rar_sets), 1)
            self.assertEqual(
                rar_sets[0].rar_set_key,
                r"collectie\album",
            )
            self.assertEqual(
                [bestand.name for bestand in rar_sets[0].volumes],
                [
                    "album.part01.rar",
                    "album.part02.rar",
                    "album.part03.rar",
                ],
            )

    def test_setnaam_en_partbreedte_worden_dynamisch_afgeleid(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            for namen in (
                ("Megaman2012.part1.rar", "Megaman2012.part2.rar"),
                (
                    "Jaarcollectie1999.part01.rar",
                    "Jaarcollectie1999.part02.rar",
                ),
                ("Andere Naam.part001.rar", "Andere Naam.part002.rar"),
            ):
                for naam in namen:
                    (root / naam).write_bytes(b"")

            rar_sets = groepeer_rar_sets(root)

            self.assertEqual(
                [rar_set.rar_set_key for rar_set in rar_sets],
                ["andere naam", "jaarcollectie1999", "megaman2012"],
            )
            self.assertEqual(
                [[pad.name for pad in rar_set.volumes] for rar_set in rar_sets],
                [
                    ["Andere Naam.part001.rar", "Andere Naam.part002.rar"],
                    [
                        "Jaarcollectie1999.part01.rar",
                        "Jaarcollectie1999.part02.rar",
                    ],
                    ["Megaman2012.part1.rar", "Megaman2012.part2.rar"],
                ],
            )


class RarInventarisVergelijkingTest(unittest.TestCase):
    def setUp(self):
        self.tijdelijke_map = tempfile.TemporaryDirectory()
        self.root = Path(self.tijdelijke_map.name)
        self.rar_map = self.root / "rar"
        self.mp3_map = self.root / "mp3"
        self.rar_map.mkdir()
        self.mp3_map.mkdir()
        self.startbestand = self.rar_map / "album.part01.rar"
        self.startbestand.write_bytes(b"")
        self.database = maak_database(self.root / "test.sqlite3")

    def tearDown(self):
        self.database.sluit()
        self.tijdelijke_map.cleanup()

    def _voer_uit(self, items):
        return voer_rar_inventory_uit(
            self.rar_map,
            self.database,
            uitvoer=io.StringIO(),
            listing_lezer=lambda rar_set: RarListingResultaat(
                items=tuple(items),
                volledig=True,
            ),
        )

    def _database_item(self):
        return self.database.verbinding.execute(
            "SELECT * FROM rar_inventory_items"
        ).fetchone()

    def test_koppelt_aanwezig_mp3_bestand(self):
        bestand = self.mp3_map / "Artiest" / "Nummer.mp3"
        bestand.parent.mkdir()
        bestand.write_bytes(b"1234")
        voeg_mp3_toe(self.database, self.mp3_map, bestand)

        overzicht = self._voer_uit([
            inventaris_item("Artiest/Nummer.mp3", 4),
        ])
        item = self._database_item()

        self.assertEqual(overzicht["aangetroffen_mp3s"], 1)
        self.assertEqual(item["ontbreekt"], 0)
        self.assertEqual(
            item["aangetroffen_rel_pad"],
            str(bestand.relative_to(self.mp3_map)),
        )
        self.assertEqual(item["grootte_afwijkend"], 0)

    def test_markeert_volledig_ontbrekend_mp3_bestand(self):
        overzicht = self._voer_uit([
            inventaris_item("Artiest/Ontbreekt.mp3", 100),
        ])
        item = self._database_item()

        self.assertEqual(overzicht["ontbrekende_mp3s"], 1)
        self.assertEqual(item["ontbreekt"], 1)
        self.assertIsNone(item["aangetroffen_rel_pad"])

    def test_markeert_afwijkende_bestandsgrootte(self):
        bestand = self.mp3_map / "Artiest" / "Nummer.mp3"
        bestand.parent.mkdir()
        bestand.write_bytes(b"123")
        voeg_mp3_toe(self.database, self.mp3_map, bestand)

        overzicht = self._voer_uit([
            inventaris_item("Artiest/Nummer.mp3", 4),
        ])
        item = self._database_item()

        self.assertEqual(overzicht["grootte_afwijkend"], 1)
        self.assertEqual(item["ontbreekt"], 0)
        self.assertEqual(item["grootte_afwijkend"], 1)

    def test_dubbele_runs_maken_geen_dubbele_items(self):
        items = [inventaris_item("Artiest/Nummer.mp3", 4)]

        self._voer_uit(items)
        eerste_id = self._database_item()["id"]
        self._voer_uit(items)
        rijen = self.database.verbinding.execute(
            "SELECT id FROM rar_inventory_items"
        ).fetchall()

        self.assertEqual(len(rijen), 1)
        self.assertEqual(rijen[0]["id"], eerste_id)


class ListingFoutIsolatieTest(unittest.TestCase):
    def test_listingfout_stopt_andere_sets_niet(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            rar_map = root / "rar"
            rar_map.mkdir()
            (rar_map / "goed.part01.rar").write_bytes(b"")
            (rar_map / "fout.part01.rar").write_bytes(b"")
            database = maak_database(root / "test.sqlite3")

            def lees_listing(rar_set):
                if rar_set.rar_set_key == "fout":
                    raise RuntimeError("beschadigde listing")

                return RarListingResultaat(
                    items=(
                        inventaris_item("Artiest/Nummer.mp3", 4),
                    ),
                    volledig=True,
                )

            try:
                overzicht = voer_rar_inventory_uit(
                    rar_map,
                    database,
                    uitvoer=io.StringIO(),
                    listing_lezer=lees_listing,
                )
                fout = database.verbinding.execute(
                    """
                    SELECT listing_fout
                    FROM rar_sets
                    WHERE rar_set_key = 'fout'
                    """
                ).fetchone()

                self.assertEqual(overzicht["rar_sets"], 2)
                self.assertEqual(overzicht["listing_fouten"], 1)
                self.assertEqual(overzicht["verwachte_mp3s"], 1)
                self.assertIn("beschadigde listing", fout["listing_fout"])
            finally:
                database.sluit()


class RarDatabaseMigratieTest(unittest.TestCase):
    def test_bestaande_database_krijgt_inventory_tabellen_en_unique_index(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            databasepad = Path(tijdelijke_map) / "bestaand.sqlite3"
            verbinding = sqlite3.connect(databasepad)
            verbinding.execute(
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
                )
                """
            )
            verbinding.commit()
            verbinding.close()

            database = SQLiteDatabase(databasepad)

            try:
                tabellen = {
                    rij["name"]
                    for rij in database.verbinding.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                indexen = database.verbinding.execute(
                    "PRAGMA index_list(rar_inventory_items)"
                ).fetchall()

                self.assertIn("rar_sets", tabellen)
                self.assertIn("rar_inventory_items", tabellen)
                self.assertTrue(any(rij["unique"] for rij in indexen))
            finally:
                database.sluit()


if __name__ == "__main__":
    unittest.main()
