import io
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from database import SQLiteDatabase, bewaar_rar_set, maak_database
from par2_verifier import (
    MAX_PROCESUITVOER,
    Par2Executable,
    classificeer_par2_resultaat,
    maak_repair_opdracht,
    vind_par2_executable,
    voer_par2_verificatie_uit,
)
from par_inventory import ParVerificatie, voer_par_inventory_uit


class Par2ExecutableDetectieTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _bestand(self, naam):
        pad = self.root / naam
        pad.parent.mkdir(parents=True, exist_ok=True)
        pad.write_bytes(b"exe")
        return pad

    def test_env_heeft_voorrang(self):
        env = self._bestand("env/par2.exe")
        path = self._bestand("path/par2.exe")
        resultaat = vind_par2_executable(
            {"PAR2_PATH": str(env)},
            which=lambda naam: str(path),
            vaste_paden=(),
        )
        self.assertEqual(resultaat.pad, env.resolve())
        self.assertEqual(resultaat.bron, "ENV")

    def test_ongeldige_env_valt_terug_op_path(self):
        path = self._bestand("pad met spaties/par2.exe")
        resultaat = vind_par2_executable(
            {"PAR2_PATH": str(self.root / "ontbreekt.exe")},
            which=lambda naam: str(path) if naam == "par2.exe" else None,
            vaste_paden=(),
        )
        self.assertEqual(resultaat.pad, path.resolve())
        self.assertEqual(resultaat.bron, "PATH")

    def test_par2j64_op_path_wordt_gevonden(self):
        executable = self._bestand("par2j64.exe")
        resultaat = vind_par2_executable(
            {},
            which=lambda naam: (
                str(executable) if naam == "par2j64.exe" else None
            ),
            vaste_paden=(),
        )
        self.assertEqual(resultaat.pad, executable.resolve())

    def test_vast_sabnzbd_pad_wordt_gevonden(self):
        executable = self._bestand("SABnzbd/win/par2/par2.exe")
        resultaat = vind_par2_executable(
            {}, which=lambda naam: None, vaste_paden=(executable,)
        )
        self.assertEqual(resultaat.bron, "FIXED_PATH")

    def test_quickpar_gui_wordt_geweigerd(self):
        gui = self._bestand("QuickPar.exe")
        self.assertIsNone(
            vind_par2_executable(
                {"PAR2_PATH": str(gui)},
                which=lambda naam: None,
                vaste_paden=(),
            )
        )

    def test_geen_tool_geeft_none_zonder_exception(self):
        self.assertIsNone(
            vind_par2_executable(
                {}, which=lambda naam: None, vaste_paden=()
            )
        )


class Par2ClassificatieTest(unittest.TestCase):
    def test_complete_standaard(self):
        resultaat = classificeer_par2_resultaat(
            "All files are correct, repair is not required.", "", 0
        )
        self.assertEqual(resultaat.status, "COMPLETE")

    def test_complete_alternatieve_tooluitvoer(self):
        resultaat = classificeer_par2_resultaat(
            "", "All files are intact.", 7
        )
        self.assertEqual(resultaat.status, "COMPLETE")

    def test_repairable_met_blocks(self):
        resultaat = classificeer_par2_resultaat(
            "10 recovery blocks are available\n"
            "You need 4 recovery blocks\nRepair is possible.",
            "",
            1,
        )
        self.assertEqual(resultaat.status, "REPAIRABLE")
        self.assertEqual(resultaat.recovery_blocks_beschikbaar, 10)
        self.assertEqual(resultaat.recovery_blocks_benodigd, 4)

    def test_not_repairable_heeft_voorrang_bij_overlap(self):
        resultaat = classificeer_par2_resultaat(
            "Repair is possible. Repair is not possible.", "", 0
        )
        self.assertEqual(resultaat.status, "NOT_REPAIRABLE")

    def test_blokkentekort_is_not_repairable(self):
        resultaat = classificeer_par2_resultaat(
            "2 recovery blocks are available\n"
            "Recovery blocks needed: 5",
            "",
            0,
        )
        self.assertEqual(resultaat.status, "NOT_REPAIRABLE")

    def test_onbekende_uitvoer_blijft_unknown(self):
        resultaat = classificeer_par2_resultaat(
            "onbekend formaat", "", 0
        )
        self.assertEqual(resultaat.status, "UNKNOWN")


class Par2ProcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.par = self.root / "set met spaties" / "démø.par2"
        self.par.parent.mkdir()
        self.par.write_bytes(b"par")
        self.exe = self.root / "tool met spaties" / "par2.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"exe")

    def tearDown(self):
        self.temp.cleanup()

    def test_veilige_read_only_subprocess_aanroep(self):
        gezien = {}

        def runner(command, **kwargs):
            gezien["command"] = command
            gezien.update(kwargs)
            return SimpleNamespace(
                stdout="All files are correct.", stderr="", returncode=0
            )

        resultaat = voer_par2_verificatie_uit(
            Par2Executable(self.exe, "ENV"), self.par, runner=runner
        )
        self.assertEqual(gezien["command"][1], "verify")
        self.assertEqual(gezien["command"][2], str(self.par.resolve()))
        self.assertFalse(gezien["shell"])
        self.assertEqual(gezien["cwd"], str(self.par.parent.resolve()))
        self.assertTrue(gezien["capture_output"])
        self.assertTrue(gezien["text"])
        self.assertEqual(gezien["timeout"], 120)
        self.assertEqual(resultaat.verification_status, "COMPLETE")
        self.assertEqual(resultaat.executable_source, "ENV")

    def test_par2j_gebruikt_v_subcommand(self):
        par2j = self.exe.with_name("par2j64.exe")
        par2j.write_bytes(b"exe")
        gezien = {}

        def runner(command, **kwargs):
            gezien["command"] = command
            return SimpleNamespace(stdout="", stderr="", returncode=9)

        resultaat = voer_par2_verificatie_uit(
            par2j, self.par, runner=runner
        )
        self.assertEqual(gezien["command"][1], "v")
        self.assertEqual(resultaat.return_code, 9)

    def test_repair_opdracht_gebruikt_juiste_subcommands(self):
        standaard = maak_repair_opdracht(self.exe, self.par)
        par2j = self.exe.with_name("par2j64.exe")
        self.assertEqual(standaard[1], "repair")
        self.assertEqual(maak_repair_opdracht(par2j, self.par)[1], "r")
        self.assertEqual(standaard[2], str(self.par.resolve()))

    def test_timeout_wordt_unknown(self):
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 120, output=b"bezig")

        resultaat = voer_par2_verificatie_uit(
            self.exe, self.par, runner=runner
        )
        self.assertEqual(resultaat.verification_status, "UNKNOWN")
        self.assertTrue(resultaat.timed_out)
        self.assertEqual(resultaat.error_type, "TIMEOUT")

    def test_procesfouten_worden_afgevangen(self):
        fouten = (
            (FileNotFoundError(), "FILE_NOT_FOUND"),
            (PermissionError(), "PERMISSION_ERROR"),
            (OSError("kapot"), "OS_ERROR"),
            (ValueError("vreemd"), "ValueError"),
        )
        for fout, verwacht in fouten:
            with self.subTest(fout=verwacht):
                def runner(*args, **kwargs):
                    raise fout

                resultaat = voer_par2_verificatie_uit(
                    self.exe, self.par, runner=runner
                )
                self.assertEqual(resultaat.verification_status, "UNKNOWN")
                self.assertEqual(resultaat.error_type, verwacht)

    def test_classificatie_gebeurt_voor_truncatie(self):
        uitvoer = "x" * (MAX_PROCESUITVOER + 100) + "All files are correct"

        def runner(*args, **kwargs):
            return SimpleNamespace(
                stdout=uitvoer, stderr="", returncode=0
            )

        resultaat = voer_par2_verificatie_uit(
            self.exe, self.par, runner=runner
        )
        self.assertEqual(resultaat.verification_status, "COMPLETE")
        self.assertLess(len(resultaat.stdout), len(uitvoer))
        self.assertIn("[afgekapt]", resultaat.stdout)


class Par2DatabaseIntegratieTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = maak_database(self.root / "test.sqlite3")
        bewaar_rar_set(
            self.database,
            "album",
            self.root / "album.part01.rar",
            True,
        )
        (self.root / "album.par2").write_bytes(b"par")

    def tearDown(self):
        self.database.sluit()
        self.temp.cleanup()

    def test_verificatiedetails_worden_opgeslagen(self):
        verificatie = ParVerificatie(
            status="COMPLETE",
            tool=r"C:\Program Files\Tool\par2.exe",
            tool_source="FIXED_PATH",
            par2_file=str(self.root / "album.par2"),
            command=("par2.exe", "verify", "album.par2"),
            return_code=0,
            stdout="All files are correct.",
            stderr="",
            verified_at="2026-07-23T12:00:00",
            duration_ms=42,
            timed_out=False,
            melding="Alle bestanden zijn correct.",
        )
        overzicht = voer_par_inventory_uit(
            self.root,
            self.database,
            uitvoer=io.StringIO(),
            verificatie_lezer=lambda par_set: verificatie,
        )
        rij = self.database.verbinding.execute(
            "SELECT * FROM par_verifications"
        ).fetchone()
        self.assertEqual(overzicht["compleet"], 1)
        self.assertEqual(rij["verification_status"], "COMPLETE")
        self.assertEqual(rij["duration_ms"], 42)
        self.assertIn("verify", rij["command"])

    def test_geen_tool_crasht_niet_en_slaat_unknown_op(self):
        uitvoer = io.StringIO()
        with patch("par_inventory.vind_par2_executable", return_value=None):
            overzicht = voer_par_inventory_uit(
                self.root, self.database, uitvoer=uitvoer
            )
        rij = self.database.verbinding.execute(
            "SELECT * FROM par_verifications"
        ).fetchone()
        self.assertEqual(overzicht["onbekend"], 1)
        self.assertEqual(rij["error_type"], "TOOL_NOT_FOUND")
        self.assertIn("PAR2-tool niet gevonden", uitvoer.getvalue())

    def test_timeout_blijft_compact_in_console(self):
        overzicht = voer_par_inventory_uit(
            self.root,
            self.database,
            uitvoer=(uitvoer := io.StringIO()),
            verificatie_lezer=lambda par_set: ParVerificatie(
                status="UNKNOWN",
                melding="PAR2-verificatie time-out.",
                timed_out=True,
                error_type="TIMEOUT",
                stdout="x" * 1000,
            ),
        )
        self.assertEqual(overzicht["onbekend"], 1)
        self.assertIn("PAR2-verificatie time-out.", uitvoer.getvalue())
        self.assertNotIn("x" * 100, uitvoer.getvalue())

    def test_tweede_run_updatet_zonder_dubbele_rij(self):
        for status in ("UNKNOWN", "REPAIRABLE"):
            voer_par_inventory_uit(
                self.root,
                self.database,
                uitvoer=io.StringIO(),
                verificatie_lezer=lambda par_set, status=status: (
                    ParVerificatie(status=status, melding=status)
                ),
            )
        rijen = self.database.verbinding.execute(
            "SELECT * FROM par_verifications"
        ).fetchall()
        self.assertEqual(len(rijen), 1)
        self.assertEqual(rijen[0]["verification_status"], "REPAIRABLE")

    def test_migratie_bestaande_database_is_idempotent(self):
        pad = self.root / "oud.sqlite3"
        verbinding = sqlite3.connect(pad)
        verbinding.execute("CREATE TABLE oud (id INTEGER PRIMARY KEY)")
        verbinding.commit()
        verbinding.close()
        eerste = SQLiteDatabase(pad)
        eerste.sluit()
        tweede = SQLiteDatabase(pad)
        kolommen = {
            rij["name"]
            for rij in tweede.verbinding.execute(
                "PRAGMA table_info(par_verifications)"
            )
        }
        tweede.sluit()
        self.assertTrue({
            "par_set_key",
            "executable_path",
            "executable_source",
            "par2_file",
            "command",
            "return_code",
            "verification_status",
            "verification_summary",
            "stdout",
            "stderr",
            "verified_at",
            "duration_ms",
            "timed_out",
            "error_type",
        }.issubset(kolommen))


if __name__ == "__main__":
    unittest.main()
