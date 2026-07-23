import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import SQLiteDatabase, bewaar_rar_set, maak_database
from par_inventory import (
    ParVerificatie,
    detecteer_par_sets,
    koppel_par_aan_rar,
    parseer_par_verificatie,
    voer_par_inventory_uit,
)


class ParDetectieTest(unittest.TestCase):
    def test_detecteert_basis_en_volume_par2(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            (root / "album.par2").write_bytes(b"basis")
            (root / "album.vol000+001.par2").write_bytes(b"volume")
            (root / "negeren.par").write_bytes(b"oud")
            sets = detecteer_par_sets(root)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].par_set_key, "album")
        self.assertEqual(len(sets[0].bestanden), 2)
        self.assertEqual(len(sets[0].recovery_volumes), 1)

    def test_koppelt_dezelfde_relatieve_setnaam(self):
        self.assertEqual(
            koppel_par_aan_rar(r"map\album", [r"map\album", "anders"]),
            r"map\album",
        )
        self.assertIsNone(
            koppel_par_aan_rar("album", [r"map\album"])
        )

    def test_rar_bestandsnaam_in_par_naam_wordt_genormaliseerd(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            (root / "album.part01.rar.par2").write_bytes(b"basis")
            (root / "album.part01.rar.vol1.par2").write_bytes(b"volume")
            par_set = detecteer_par_sets(root)[0]
        self.assertEqual(par_set.par_set_key, "album")
        self.assertEqual(len(par_set.recovery_volumes), 1)

    def test_meerdere_recovery_volumes_worden_geteld(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            for naam in (
                "set.par2",
                "set.vol000+002.par2",
                "set.vol002+004.par2",
            ):
                (root / naam).write_bytes(b"x")
            par_set = detecteer_par_sets(root)[0]
        self.assertEqual(len(par_set.bestanden), 3)
        self.assertEqual(len(par_set.recovery_volumes), 2)


class ParInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = maak_database(self.root / "test.sqlite3")

    def tearDown(self):
        self.database.sluit()
        self.temp.cleanup()

    def _rar(self, sleutel="album"):
        bewaar_rar_set(
            self.database,
            sleutel,
            self.root / f"{sleutel}.part01.rar",
            True,
        )

    def test_ontbrekende_par_wordt_no_par(self):
        self._rar()
        overzicht = voer_par_inventory_uit(
            self.root, self.database, uitvoer=io.StringIO()
        )
        rij = self.database.verbinding.execute(
            "SELECT * FROM par_inventory"
        ).fetchone()
        self.assertEqual(rij["status"], "NO_PAR")
        self.assertEqual(overzicht["geen_par"], 1)

    def test_ontbrekende_rar_wordt_no_rar(self):
        (self.root / "los.par2").write_bytes(b"par")
        overzicht = voer_par_inventory_uit(
            self.root, self.database, uitvoer=io.StringIO()
        )
        rij = self.database.verbinding.execute(
            "SELECT * FROM par_inventory"
        ).fetchone()
        self.assertEqual(rij["status"], "NO_RAR")
        self.assertEqual(overzicht["geen_rar"], 1)

    def test_onbekende_verificatie_blijft_unknown(self):
        self._rar()
        (self.root / "album.par2").write_bytes(b"par")
        overzicht = voer_par_inventory_uit(
            self.root,
            self.database,
            uitvoer=io.StringIO(),
            verificatie_lezer=lambda par_set: ParVerificatie(
                status="UNKNOWN",
                melding="Geen bruikbare verificatie-uitvoer.",
            ),
        )
        rij = self.database.verbinding.execute(
            "SELECT * FROM par_inventory"
        ).fetchone()
        self.assertEqual(rij["status"], "UNKNOWN")
        self.assertEqual(overzicht["onbekend"], 1)

    def test_recovery_blocks_bepalen_repairable(self):
        verificatie = parseer_par_verificatie(
            "You have 10 recovery blocks available.\n"
            "You need 4 recovery blocks to be able to repair.\n"
            "Repair is possible."
        )
        self.assertEqual(verificatie.status, "REPAIRABLE")
        self.assertEqual(verificatie.recovery_blocks_beschikbaar, 10)
        self.assertEqual(verificatie.recovery_blocks_benodigd, 4)


class ParMigratieTest(unittest.TestCase):
    def test_bestaande_database_krijgt_par_inventory(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            pad = Path(tijdelijke_map) / "bestaand.sqlite3"
            verbinding = sqlite3.connect(pad)
            verbinding.execute(
                "CREATE TABLE bestaand (id INTEGER PRIMARY KEY)"
            )
            verbinding.commit()
            verbinding.close()
            database = SQLiteDatabase(pad)
            kolommen = {
                rij["name"]
                for rij in database.verbinding.execute(
                    "PRAGMA table_info(par_inventory)"
                )
            }
            database.sluit()
        self.assertTrue({
            "par_set_key",
            "gekoppelde_rar_set_key",
            "aantal_par_bestanden",
            "aantal_recovery_volumes",
            "recovery_blocks_beschikbaar",
            "recovery_blocks_benodigd",
            "status",
        }.issubset(kolommen))


if __name__ == "__main__":
    unittest.main()
