import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import SQLiteDatabase
from database import bewaar_rar_set
from database import voeg_mp3_toe
from database import maak_database
from database import vergelijk_rar_inventory
from database import vervang_rar_inventory_items
from database import verkrijg_recovery_items
from database import zet_ffmpeg_status
from database import zet_nul_bytes
from database import zet_rar_status
from paden import normaliseer_relatief_pad
from paden import normaliseer_relatief_pad_sleutel
from recovery import genereer_recovery_items


class RecoveryGeneratieTest(unittest.TestCase):
    def setUp(self):
        self.tijdelijke_map = tempfile.TemporaryDirectory()
        self.root = Path(self.tijdelijke_map.name)
        self.mp3_map = self.root / "mp3"
        self.mp3_map.mkdir()
        self.database = maak_database(self.root / "test.sqlite3")

    def tearDown(self):
        self.database.sluit()
        self.tijdelijke_map.cleanup()

    def _voeg_mp3_toe(self, relatief_pad, inhoud=b"1234"):
        bestand = self.mp3_map / Path(relatief_pad)
        bestand.parent.mkdir(parents=True, exist_ok=True)
        bestand.write_bytes(inhoud)
        voeg_mp3_toe(
            self.database,
            self.mp3_map,
            bestand,
        )
        return bestand

    def _voeg_inventaris_toe(self, relatief_pad, grootte=4):
        genormaliseerd = normaliseer_relatief_pad(relatief_pad)
        bestandsnaam = genormaliseerd.rsplit("\\", 1)[-1]
        verwachte_map = (
            genormaliseerd.rsplit("\\", 1)[0]
            if "\\" in genormaliseerd
            else ""
        )
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
                "verwacht_rel_pad": genormaliseerd,
                "verwacht_rel_pad_norm":
                    normaliseer_relatief_pad_sleutel(genormaliseerd),
                "verwachte_map": verwachte_map,
                "verwachte_bestandsnaam": bestandsnaam,
                "verwachte_grootte": grootte,
                "verwachte_crc32": "A1B2C3D4",
                "verwachte_modified": None,
            }],
        )
        vergelijk_rar_inventory(self.database)

    def _genereer(self):
        return genereer_recovery_items(
            self.database,
            uitvoer=io.StringIO(),
        )

    def _item(self):
        items = verkrijg_recovery_items(self.database)
        self.assertEqual(len(items), 1)
        return items[0]

    def test_ontbrekend_bestand(self):
        self._voeg_inventaris_toe("Artiest/Ontbreekt.mp3")

        overzicht = self._genereer()
        item = self._item()

        self.assertEqual(overzicht["ontbreekt"], 1)
        self.assertEqual(item["probleem_type"], "ontbreekt")
        self.assertEqual(item["feit_ontbreekt"], 1)
        self.assertIsNone(item["mp3_id"])
        self.assertIsNotNone(item["inventaris_id"])

    def test_nul_bytes(self):
        bestand = self._voeg_mp3_toe("Artiest/Nul.mp3", b"")
        zet_nul_bytes(self.database, self.mp3_map, bestand)

        overzicht = self._genereer()
        item = self._item()

        self.assertEqual(overzicht["nul_bytes"], 1)
        self.assertEqual(item["probleem_type"], "nul_bytes")
        self.assertEqual(item["feit_nul_bytes"], 1)
        self.assertIsNotNone(item["mp3_id"])

    def test_ffmpeg_corrupt(self):
        self._voeg_mp3_toe("Artiest/Corrupt.mp3")
        zet_ffmpeg_status(
            self.database,
            r"Artiest\Corrupt.mp3",
            "ERROR",
            "Decode error",
            "frame beschadigd",
        )

        overzicht = self._genereer()
        item = self._item()

        self.assertEqual(overzicht["corrupt"], 1)
        self.assertEqual(item["probleem_type"], "corrupt")
        self.assertEqual(item["feit_corrupt"], 1)
        self.assertIn("Decode error", item["ffmpeg_fout"])

    def test_grootteafwijking(self):
        self._voeg_mp3_toe("Artiest/Kort.mp3", b"123")
        self._voeg_inventaris_toe("Artiest/Kort.mp3", grootte=4)

        overzicht = self._genereer()
        item = self._item()

        self.assertEqual(overzicht["grootte_afwijking"], 1)
        self.assertEqual(item["probleem_type"], "grootte_afwijking")
        self.assertEqual(item["feit_grootte_afwijking"], 1)

    def test_rar_crc(self):
        self._voeg_mp3_toe("Artiest/CRC.mp3")
        zet_rar_status(
            self.database,
            r"Artiest\CRC.mp3",
            "ERROR",
            "CRC Failed",
        )

        overzicht = self._genereer()
        item = self._item()

        self.assertEqual(overzicht["rar_crc"], 1)
        self.assertEqual(item["probleem_type"], "rar_crc")
        self.assertEqual(item["feit_rar_crc"], 1)
        self.assertEqual(item["rar_fout"], "CRC Failed")

    def test_gecombineerd_probleem_gebruikt_prioriteit(self):
        self._voeg_mp3_toe("Artiest/Combinatie.mp3", b"123")
        self._voeg_inventaris_toe(
            "Artiest/Combinatie.mp3",
            grootte=4,
        )
        zet_ffmpeg_status(
            self.database,
            r"Artiest\Combinatie.mp3",
            "ERROR",
            "Decode error",
            "frame beschadigd",
        )

        overzicht = self._genereer()
        item = self._item()

        self.assertEqual(overzicht["corrupt"], 1)
        self.assertEqual(item["probleem_type"], "corrupt")
        self.assertEqual(item["feit_corrupt"], 1)
        self.assertEqual(item["feit_grootte_afwijking"], 1)

    def test_incremental_update_behoudt_id_en_workflowvlaggen(self):
        bestand = self._voeg_mp3_toe("Artiest/Update.mp3", b"")
        zet_nul_bytes(self.database, self.mp3_map, bestand)
        self._genereer()
        eerste = self._item()
        self.database.verbinding.execute(
            """
            UPDATE recovery_items
            SET spotify_verwerkt = 1,
                download_verwerkt = 1,
                geplaatst = 1
            WHERE id = ?
            """,
            (eerste["id"],),
        )
        self.database.verbinding.commit()
        zet_ffmpeg_status(
            self.database,
            "Artiest\\Update.mp3",
            "ERROR",
            "Decode error",
            "frame beschadigd",
        )

        self._genereer()
        bijgewerkt = self._item()

        self.assertEqual(bijgewerkt["id"], eerste["id"])
        self.assertEqual(bijgewerkt["probleem_type"], "corrupt")
        self.assertEqual(bijgewerkt["spotify_verwerkt"], 1)
        self.assertEqual(bijgewerkt["download_verwerkt"], 1)
        self.assertEqual(bijgewerkt["geplaatst"], 1)

    def test_verwijdert_opgelost_probleem(self):
        bestand = self._voeg_mp3_toe("Artiest/Opgelost.mp3", b"")
        zet_nul_bytes(self.database, self.mp3_map, bestand)
        self._genereer()
        self.assertEqual(len(verkrijg_recovery_items(self.database)), 1)
        self.database.verbinding.execute(
            """
            UPDATE mp3_bestanden
            SET nul_bytes = 0,
                ffmpeg_status = 'OK',
                ffmpeg_type = NULL,
                ffmpeg_melding = NULL,
                rar_status = 'NIET_GECONTROLEERD',
                rar_type = NULL
            WHERE relatief_pad = ?
            """,
            (r"Artiest\Opgelost.mp3",),
        )
        self.database.verbinding.commit()

        overzicht = self._genereer()

        self.assertEqual(overzicht["totaal"], 0)
        self.assertEqual(verkrijg_recovery_items(self.database), [])


class RecoveryDatabaseMigratieTest(unittest.TestCase):
    def test_migreert_bestaande_database(self):
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
            verbinding.execute(
                """
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
                )
                """
            )
            verbinding.commit()
            verbinding.close()

            database = SQLiteDatabase(databasepad)

            try:
                recovery_kolommen = {
                    rij["name"]
                    for rij in database.verbinding.execute(
                        "PRAGMA table_info(recovery_items)"
                    )
                }
                mp3 = database.verbinding.execute(
                    """
                    SELECT id
                    FROM mp3_bestanden
                    WHERE relatief_pad = 'nummer.mp3'
                    """
                ).fetchone()
                indexen = database.verbinding.execute(
                    "PRAGMA index_list(recovery_items)"
                ).fetchall()

                self.assertIn("probleem_type", recovery_kolommen)
                self.assertIn("feit_corrupt", recovery_kolommen)
                self.assertIsNotNone(mp3["id"])
                self.assertTrue(any(rij["unique"] for rij in indexen))
            finally:
                database.sluit()


if __name__ == "__main__":
    unittest.main()
