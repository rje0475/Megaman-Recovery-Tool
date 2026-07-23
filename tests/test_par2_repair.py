import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from database import maak_database
from par2_repair import Par2RepairFout, voer_par2_reparatie_uit
from par2_verifier import Par2Executable, Par2VerificatieResultaat


class Par2RepairTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.par2 = self.root / "album.par2"
        self.par2.write_bytes(b"par2")
        self.exe = self.root / "par2.exe"
        self.exe.write_bytes(b"exe")
        self.tool = Par2Executable(self.exe.resolve(), "TEST")
        self.db_pad = self.root / "test.sqlite3"
        self.database = maak_database(self.db_pad)
        self._zet_status("REPAIRABLE")

    def tearDown(self):
        self.database.sluit()
        self.temp.cleanup()

    def _zet_status(self, status):
        self.database.verbinding.execute("DELETE FROM par_inventory")
        self.database.verbinding.execute(
            """
            INSERT INTO par_inventory (
              par_set_key, par_startbestand, aantal_par_bestanden,
              status, bijgewerkt_op
            ) VALUES ('album', ?, 1, ?, '2026-07-23T12:00:00')
            """,
            (str(self.par2), status),
        )
        self.database.verbinding.commit()

    def _verificatie(self, status="COMPLETE"):
        return Par2VerificatieResultaat(
            executable_path=str(self.exe.resolve()),
            executable_source="TEST",
            par2_file=str(self.par2.resolve()),
            command=(str(self.exe.resolve()), "verify", str(self.par2)),
            return_code=0,
            verification_status=status,
            verification_summary=(
                "Alle bestanden zijn correct."
                if status == "COMPLETE" else status
            ),
            stdout="verify stdout",
            stderr="",
            verified_at="2026-07-23T12:01:00",
            duration_ms=10,
            timed_out=False,
            error_type=None,
        )

    def _repair_rij(self):
        return self.database.verbinding.execute(
            "SELECT * FROM par_repair_results ORDER BY id DESC"
        ).fetchone()

    def test_repairable_wordt_succesvol_gerepareerd_en_geverifieerd(self):
        uitvoer = io.StringIO()
        runner = Mock(return_value=SimpleNamespace(
            returncode=0, stdout="repair stdout", stderr=""
        ))
        verifier = Mock(return_value=self._verificatie())

        overzicht = voer_par2_reparatie_uit(
            self.root, self.db_pad, uitvoer=uitvoer,
            executable=self.tool, runner=runner, verifier=verifier,
            nu_functie=lambda: datetime(2026, 7, 23, 12, 0, 0),
        )

        self.assertEqual(overzicht.gerepareerd, 1)
        command = runner.call_args.args[0]
        self.assertEqual(command[1], "repair")
        self.assertEqual(command[2], str(self.par2.resolve()))
        self.assertFalse(runner.call_args.kwargs["shell"])
        verifier.assert_called_once_with(self.tool, self.par2)
        self.assertIn("Repair gestart", uitvoer.getvalue())
        self.assertIn("Repair voltooid", uitvoer.getvalue())
        self.assertIn("Opnieuw verifiëren", uitvoer.getvalue())
        self.assertIn("Eindstatus [album]: COMPLETE", uitvoer.getvalue())
        rij = self._repair_rij()
        self.assertEqual(rij["result"], "SUCCESS")
        self.assertEqual(rij["exit_code"], 0)
        self.assertEqual(rij["final_status"], "COMPLETE")
        self.assertEqual(rij["stdout"], "repair stdout")
        self.assertIsNotNone(rij["started_at"])
        self.assertIsNotNone(rij["finished_at"])
        status = self.database.verbinding.execute(
            "SELECT status FROM par_inventory WHERE par_set_key = 'album'"
        ).fetchone()["status"]
        verificatie = self.database.verbinding.execute(
            """
            SELECT verification_status FROM par_verifications
            WHERE par_set_key = 'album'
            """
        ).fetchone()["verification_status"]
        self.assertEqual(status, "COMPLETE")
        self.assertEqual(verificatie, "COMPLETE")

    def test_repairable_repair_mislukt_en_wordt_opgeslagen(self):
        verifier = Mock()
        overzicht = voer_par2_reparatie_uit(
            self.root, self.db_pad, uitvoer=io.StringIO(),
            executable=self.tool,
            runner=lambda *args, **kwargs: SimpleNamespace(
                returncode=2, stdout="bezig", stderr="repair fout"
            ),
            verifier=verifier,
        )
        self.assertEqual(overzicht.mislukt, 1)
        verifier.assert_not_called()
        rij = self._repair_rij()
        self.assertEqual(rij["result"], "FAILED")
        self.assertEqual(rij["exit_code"], 2)
        self.assertEqual(rij["stderr"], "repair fout")
        self.assertEqual(rij["last_error"], "repair fout")
        self.assertEqual(rij["final_status"], "REPAIRABLE")

    def test_complete_wordt_overgeslagen(self):
        self._zet_status("COMPLETE")
        runner = Mock()
        overzicht = voer_par2_reparatie_uit(
            self.root, self.db_pad, uitvoer=io.StringIO(),
            executable=self.tool, runner=runner,
        )
        self.assertEqual(overzicht.overgeslagen, 1)
        runner.assert_not_called()
        self.assertEqual(self._repair_rij()["result"], "SKIPPED")

    def test_not_repairable_wordt_overgeslagen(self):
        self._zet_status("NOT_REPAIRABLE")
        runner = Mock()
        overzicht = voer_par2_reparatie_uit(
            self.root, self.db_pad, uitvoer=io.StringIO(),
            executable=self.tool, runner=runner,
        )
        self.assertEqual(overzicht.overgeslagen, 1)
        runner.assert_not_called()
        self.assertEqual(
            self._repair_rij()["final_status"], "NOT_REPAIRABLE"
        )

    def test_ontbrekende_par2_tool_geeft_nette_fout(self):
        with patch("par2_repair.vind_par2_executable", return_value=None):
            with self.assertRaisesRegex(
                Par2RepairFout, "PAR2-tool niet gevonden"
            ):
                voer_par2_reparatie_uit(
                    self.root, self.db_pad, uitvoer=io.StringIO()
                )

    def test_succesvolle_repair_met_onvolledige_eindstatus_mislukt(self):
        overzicht = voer_par2_reparatie_uit(
            self.root, self.db_pad, uitvoer=io.StringIO(),
            executable=self.tool,
            runner=lambda *args, **kwargs: SimpleNamespace(
                returncode=0, stdout="", stderr=""
            ),
            verifier=lambda *args: self._verificatie("REPAIRABLE"),
        )
        self.assertEqual(overzicht.mislukt, 1)
        self.assertEqual(self._repair_rij()["result"], "FAILED")
        self.assertEqual(self._repair_rij()["final_status"], "REPAIRABLE")


if __name__ == "__main__":
    unittest.main()
