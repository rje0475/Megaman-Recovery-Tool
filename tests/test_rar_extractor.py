import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from database import bewaar_rar_set, maak_database
from rar_extractor import (
    ExtractieFout,
    ExtractieTool,
    vind_extractie_tool,
    voer_extractie_uit,
)


class RarExtractorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_pad = self.root / "test.sqlite3"
        self.database = maak_database(self.db_pad)
        self.start = self.root / "album.part01.rar"
        self.start.write_bytes(b"deel 1")
        (self.root / "album.part02.rar").write_bytes(b"deel 2")
        bewaar_rar_set(
            self.database, "album", self.start, True
        )

    def tearDown(self):
        self.database.sluit()
        self.temp.cleanup()

    def _verificatie(self, status):
        self.database.verbinding.execute(
            """
            INSERT INTO par_inventory (
              par_set_key, gekoppelde_rar_set_key, par_startbestand,
              status, bijgewerkt_op
            ) VALUES ('album', 'album', 'album.par2', ?, '2026-07-23')
            """,
            (status,),
        )
        self.database.verbinding.execute(
            """
            INSERT INTO par_verifications (
              par_set_key, par2_file, command, verification_status,
              verification_summary, verified_at, duration_ms
            ) VALUES (
              'album', 'album.par2', '[]', ?, ?, '2026-07-23', 1
            )
            """,
            (status, status),
        )
        self.database.verbinding.commit()

    def _tool(self, type_="7ZIP"):
        executable = self.root / (
            "7z.exe" if type_ == "7ZIP" else "unrar.exe"
        )
        executable.write_bytes(b"exe")
        return ExtractieTool(executable, type_)

    def _resultaten(self):
        verbinding = sqlite3.connect(self.db_pad)
        verbinding.row_factory = sqlite3.Row
        try:
            return verbinding.execute(
                "SELECT * FROM extraction_results ORDER BY id"
            ).fetchall()
        finally:
            verbinding.close()

    def test_complete_pakt_part01_uit_naar_extracted(self):
        self._verificatie("COMPLETE")
        gezien = {}

        def runner(command, **kwargs):
            gezien["command"] = command
            gezien.update(kwargs)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        overzicht = voer_extractie_uit(
            self.root, self.db_pad, uitvoer=io.StringIO(),
            tool=self._tool(), runner=runner,
        )

        self.assertEqual(overzicht.uitgepakt, 1)
        self.assertEqual(gezien["command"][1], "x")
        self.assertEqual(gezien["command"][2], str(self.start.resolve()))
        self.assertIn(f"-o{self.root / 'extracted'}", gezien["command"])
        self.assertIn("-aos", gezien["command"])
        self.assertFalse(gezien["shell"])
        self.assertEqual(self._resultaten()[0]["extraction_status"], "EXTRACTED")

    def test_onveilige_statussen_worden_zonder_tooloproep_overgeslagen(self):
        for status in ("REPAIRABLE", "NOT_REPAIRABLE", "UNKNOWN"):
            with self.subTest(status=status):
                self.database.verbinding.execute(
                    "DELETE FROM par_verifications"
                )
                self.database.verbinding.execute("DELETE FROM par_inventory")
                self.database.verbinding.commit()
                self._verificatie(status)
                aangeroepen = []
                uitvoer = io.StringIO()
                overzicht = voer_extractie_uit(
                    self.root, self.db_pad, uitvoer=uitvoer,
                    tool=self._tool(),
                    runner=lambda *args, **kwargs: aangeroepen.append(args),
                )
                self.assertEqual(overzicht.overgeslagen, 1)
                self.assertEqual(aangeroepen, [])
                self.assertIn(status, uitvoer.getvalue())
                self.assertIn("COMPLETE is vereist", uitvoer.getvalue())

    def test_ontbrekende_verificatie_is_unknown_en_maakt_geen_doelmap(self):
        doelmap = self.root / "extracted"
        overzicht = voer_extractie_uit(
            self.root, self.db_pad, uitvoer=io.StringIO(),
            tool=self._tool(),
        )
        self.assertEqual(overzicht.overgeslagen, 1)
        self.assertFalse(doelmap.exists())
        rij = self._resultaten()[0]
        self.assertEqual(rij["par2_status"], "UNKNOWN")
        self.assertEqual(rij["extraction_status"], "SKIPPED")

    def test_unrar_command_en_mislukking_worden_opgeslagen(self):
        self._verificatie("COMPLETE")
        gezien = {}

        def runner(command, **kwargs):
            gezien["command"] = command
            return SimpleNamespace(
                returncode=3, stdout="", stderr="kapot"
            )

        overzicht = voer_extractie_uit(
            self.root, self.db_pad, uitvoer=io.StringIO(),
            tool=self._tool("UNRAR"), runner=runner,
        )
        self.assertEqual(overzicht.mislukt, 1)
        self.assertEqual(gezien["command"][1:3], ["x", "-o-"])
        rij = self._resultaten()[0]
        self.assertEqual(rij["return_code"], 3)
        self.assertEqual(rij["stderr"], "kapot")
        self.assertEqual(rij["executable_type"], "UNRAR")

    def test_doelmap_mag_niet_de_bronmap_zijn(self):
        with self.assertRaisesRegex(ExtractieFout, "niet de bronmap"):
            voer_extractie_uit(
                self.root, self.db_pad, doelmap=self.root
            )

    def test_ontbrekende_database_geeft_duidelijke_fout(self):
        with self.assertRaisesRegex(ExtractieFout, "Database"):
            voer_extractie_uit(
                self.root, self.root / "ontbreekt.sqlite3"
            )

    def test_detectie_geeft_7zip_voorrang_op_unrar(self):
        zeven_zip = self.root / "7z.exe"
        unrar = self.root / "unrar.exe"
        zeven_zip.write_bytes(b"exe")
        unrar.write_bytes(b"exe")
        tool = vind_extractie_tool({
            "SEVEN_ZIP_PATH": str(zeven_zip),
            "UNRAR_PATH": str(unrar),
        }, which=lambda naam: None)
        self.assertEqual(tool.type, "7ZIP")
        self.assertEqual(tool.pad, zeven_zip.resolve())


if __name__ == "__main__":
    unittest.main()
