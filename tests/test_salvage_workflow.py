import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.external_tools import ToolResultaat, detecteer_tool
from core.salvage_compare import (
    VergelijkItem,
    VergelijkResultaat,
    vergelijk_extractie,
)
from core.salvage_extractor import salvage_extract
from core.salvage_workflow import (
    ArchiveSet,
    SalvageFout,
    _resolveer_set_volumes,
    _synchroniseer_recovery,
    ontdek_archive_sets,
    voer_salvage_workflow_uit,
)
from core.winrar_recovery import voer_winrar_recovery_uit
from database import bewaar_rar_set, maak_database


class ToolDetectieTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _exe(self, naam):
        pad = self.root / naam
        pad.write_bytes(b"exe")
        return pad

    def test_env_standaard_path_en_ontbrekend(self):
        env = self._exe("env.exe")
        standaard = self._exe("standaard.exe")
        path = self._exe("path.exe")
        resultaat = detecteer_tool(
            "Tool", "TOOL_PATH", (standaard,), ("tool",),
            omgeving={"TOOL_PATH": str(env)}, which=lambda _: str(path),
        )
        self.assertEqual((resultaat.pad, resultaat.bron),
                         (env.resolve(), "ENV"))
        resultaat = detecteer_tool(
            "Tool", "TOOL_PATH", (standaard,), ("tool",),
            omgeving={}, which=lambda _: str(path),
        )
        self.assertEqual(resultaat.bron, "STANDARD")
        resultaat = detecteer_tool(
            "Tool", "TOOL_PATH", (), ("tool",),
            omgeving={}, which=lambda _: str(path),
        )
        self.assertEqual(resultaat.bron, "PATH")
        resultaat = detecteer_tool(
            "Tool", "TOOL_PATH", (), ("tool",),
            omgeving={}, which=lambda _: None,
        )
        self.assertFalse(resultaat.beschikbaar)


class WinRarTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / "set met spaties.part01.rar"
        self.archive.write_bytes(b"origineel")
        self.exe = self.root / "WinRAR.exe"
        self.exe.write_bytes(b"exe")
        self.tool = ToolResultaat(
            "WinRAR", self.exe.resolve(), True, "TEST"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_rebuilt_wordt_gekozen_en_origineel_blijft_gelijk(self):
        origineel = self.archive.read_bytes()

        def runner(command, **kwargs):
            Path(kwargs["cwd"], "rebuilt.set met spaties.part01.rar").write_bytes(
                b"hersteld"
            )
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        resultaat = voer_winrar_recovery_uit(
            (self.archive,), self.root / "workspace",
            tool=self.tool, runner=runner,
        )
        self.assertEqual(resultaat.status, "SUCCESS")
        self.assertTrue(resultaat.gekozen_archive.name.startswith("rebuilt."))
        self.assertEqual(self.archive.read_bytes(), origineel)
        self.assertIsInstance(resultaat.gekozen_archive, Path)

    def test_exitcode_fout_met_rebuilt_is_partial(self):
        def runner(command, **kwargs):
            Path(kwargs["cwd"], "rebuilt.set.part01.rar").write_bytes(b"x")
            return SimpleNamespace(returncode=3, stdout="", stderr="fout")

        resultaat = voer_winrar_recovery_uit(
            (self.archive,), self.root / "workspace",
            tool=self.tool, runner=runner,
        )
        self.assertEqual(resultaat.status, "PARTIAL")

    def test_geen_output_is_failed(self):
        resultaat = voer_winrar_recovery_uit(
            (self.archive,), self.root / "workspace", tool=self.tool,
            runner=lambda *a, **k: SimpleNamespace(
                returncode=0, stdout="", stderr=""
            ),
        )
        self.assertEqual(resultaat.status, "FAILED")


class ExtractieTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / "set met spaties.rar"
        self.archive.write_bytes(b"rar")
        exe = self.root / "7z.exe"
        exe.write_bytes(b"exe")
        self.tool = ToolResultaat("7-Zip", exe, True, "TEST")

    def tearDown(self):
        self.temp.cleanup()

    def _runner(self, returncode, inhoud=True, stderr=""):
        def runner(command, **kwargs):
            uitvoer = next(a[2:] for a in command if a.startswith("-o"))
            if inhoud:
                doel = Path(uitvoer) / "2006" / "Week01"
                doel.mkdir(parents=True, exist_ok=True)
                (doel / "track.mp3").write_bytes(b"mp3")
            return SimpleNamespace(
                returncode=returncode, stdout="", stderr=stderr
            )
        return runner

    def test_volledig_partial_crc_en_failed(self):
        volledig = salvage_extract(
            self.archive, self.root / "volledig", self.tool,
            self._runner(0),
        )
        self.assertEqual(volledig.status, "SUCCESS")
        partial = salvage_extract(
            self.archive, self.root / "partial", self.tool,
            self._runner(2, stderr="ERROR: CRC Failed"),
        )
        self.assertEqual(partial.status, "PARTIAL")
        self.assertTrue(partial.data_fouten)
        failed = salvage_extract(
            self.archive, self.root / "failed", self.tool,
            self._runner(2, inhoud=False),
        )
        self.assertEqual(failed.status, "FAILED")


class ComparatorTest(unittest.TestCase):
    def test_alle_statussen_submappen_casefold_en_extra(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            map_ = root / "2006" / "Week01"
            map_.mkdir(parents=True)
            (map_ / "OK.MP3").write_bytes(b"123")
            (map_ / "zero.mp3").write_bytes(b"")
            (map_ / "size.mp3").write_bytes(b"1234")
            (map_ / "bad.mp3").write_bytes(b"x")
            (map_ / "extra.mp3").write_bytes(b"x")
            verwacht = [
                {"verwacht_rel_pad": r"2006\week01\ok.mp3",
                 "verwachte_grootte": 3},
                {"verwacht_rel_pad": r"2006\Week01\missing.mp3",
                 "verwachte_grootte": 1},
                {"verwacht_rel_pad": r"2006\Week01\zero.mp3",
                 "verwachte_grootte": 1},
                {"verwacht_rel_pad": r"2006\Week01\size.mp3",
                 "verwachte_grootte": 5},
                {"verwacht_rel_pad": r"2006\Week01\bad.mp3",
                 "verwachte_grootte": 1},
            ]
            resultaat = vergelijk_extractie(
                verwacht, root,
                mp3_lezer=lambda p: p.name.casefold() != "bad.mp3",
            )
            self.assertEqual(
                [item.status for item in resultaat.items],
                ["OK", "MISSING", "ZERO_BYTE", "SIZE_MISMATCH", "UNREADABLE"],
            )
            self.assertEqual(len(resultaat.extras), 1)


class RecoverySynchronisatieTest(unittest.TestCase):
    def test_alleen_defecten_en_handmatige_keuzes_blijven(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            db = maak_database(Path(tijdelijke_map) / "test.db")
            try:
                vergelijking = VergelijkResultaat((
                    VergelijkItem("A - Goed.mp3", "OK", Path("goed"), 1, 1, "ok"),
                    VergelijkItem(
                        "B - Mist.mp3", "MISSING", None, 1, None,
                        "missing_after_salvage",
                    ),
                ), ())
                aantal = _synchroniseer_recovery(db, "set", vergelijking)
                self.assertEqual(aantal, 1)
                rijen = db.verbinding.execute(
                    "SELECT * FROM recovery_items"
                ).fetchall()
                self.assertEqual(len(rijen), 1)
                self.assertEqual(rijen[0]["identiteit_reden"],
                                 "missing_after_salvage")
            finally:
                db.sluit()


class OrchestratorTest(unittest.TestCase):
    def test_model_met_82_part_volumes_blijft_compleet(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            volumes = []
            for nummer in range(1, 83):
                volume = root / f"Megaman2006.part{nummer:02d}.rar"
                volume.write_bytes(b"x")
                volumes.append(volume)
            set_ = ArchiveSet("megaman2006", volumes[0], tuple(volumes))
            resultaat = _resolveer_set_volumes(
                set_, root, root / "megaman_salvage", StringIO()
            )
            self.assertEqual(len(resultaat.volumes), 82)
            self.assertEqual(resultaat.volumes[0].name,
                             "Megaman2006.part01.rar")
            self.assertEqual(resultaat.volumes[-1].name,
                             "Megaman2006.part82.rar")

    def test_leeg_model_vindt_82_part_volumes_met_fallbackscan(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            for nummer in range(1, 83):
                (root / f"Megaman2006.part{nummer:02d}.rar").write_bytes(b"x")
            log = StringIO()
            resultaat = _resolveer_set_volumes(
                ArchiveSet("megaman2006", root / "Megaman2006.part01.rar", ()),
                root, root / "megaman_salvage", log,
            )
            self.assertEqual(len(resultaat.volumes), 82)
            self.assertIn("model=0", log.getvalue())
            self.assertIn("fallback=82", log.getvalue())

    def test_leeg_model_gebruikt_fallback_en_negeert_old_en_workspace(self):
        with tempfile.TemporaryDirectory(
            prefix="4fe20a6a4f204822ed17e88d.#2."
        ) as tijdelijke_map:
            root = Path(tijdelijke_map)
            for naam in (
                "Megaman2006.part01.rar",
                "Megaman2006.part02.rar",
                "Megaman2006.part07.old",
                "Megaman2006.part07.rar",
                "Megaman2006.part17.old",
                "Megaman2006.part17.rar",
                "Megaman2006.part82.rar",
                "Megaman2006.par2",
            ):
                (root / naam).write_bytes(b"x")
            workspace = root / "megaman_salvage"
            workspace.mkdir()
            (workspace / "Megaman2006.part03.rar").write_bytes(b"x")
            log = StringIO()
            resultaat = _resolveer_set_volumes(
                ArchiveSet(
                    "MEGAMAN2006", root / "MEGAMAN2006.PART01.RAR", ()
                ),
                root, workspace, log,
            )
            self.assertEqual(
                [pad.name for pad in resultaat.volumes],
                [
                    "Megaman2006.part01.rar",
                    "Megaman2006.part02.rar",
                    "Megaman2006.part07.rar",
                    "Megaman2006.part17.rar",
                    "Megaman2006.part82.rar",
                ],
            )
            self.assertIn("fallback=5", log.getvalue())
            self.assertIn(".old genegeerd=2", log.getvalue())
            self.assertIn(f"bronmap={root.resolve()}", log.getvalue())

    def test_part_volumes_worden_numeriek_en_case_insensitive_gevonden(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            for naam in (
                "MegaMan.PART10.RAR",
                "MegaMan.PART02.RAR",
                "MegaMan.PART01.RAR",
            ):
                (root / naam).write_bytes(b"x")
            sets = ontdek_archive_sets(root)
            self.assertEqual(
                [pad.name for pad in sets[0].volumes],
                ["MegaMan.PART01.RAR", "MegaMan.PART02.RAR",
                 "MegaMan.PART10.RAR"],
            )

    def test_pas_na_lege_fallback_wordt_volume_fout_gegeven(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            log = StringIO()
            with self.assertRaisesRegex(
                SalvageFout, "RAR-set bevat geen volumes"
            ):
                _resolveer_set_volumes(
                    ArchiveSet("megaman2006", root / "missing.part01.rar", ()),
                    root, root / "megaman_salvage", log,
                )
            self.assertIn("model=0", log.getvalue())
            self.assertIn("fallback=0", log.getvalue())

    def test_not_repairable_gaat_door_naar_winrar_en_7zip(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            archive = root / "set.part01.rar"
            archive.write_bytes(b"rar")
            db_pad = root / "test.db"
            db = maak_database(db_pad)
            bewaar_rar_set(db, "set", archive, True)
            db.verbinding.execute(
                """
                INSERT INTO par_inventory (
                  par_set_key, gekoppelde_rar_set_key, par_startbestand,
                  status, bijgewerkt_op
                ) VALUES ('set', 'set', 'set.par2', 'NOT_REPAIRABLE', 'nu')
                """
            )
            db.verbinding.commit()
            db.sluit()
            tool = ToolResultaat("test", root / "tool.exe", True, "TEST")
            tool.pad.write_bytes(b"exe")
            vergelijking = VergelijkResultaat((
                VergelijkItem("track.mp3", "MISSING", None, 1, None,
                               "missing_after_salvage"),
            ), ())
            with (
                patch("core.salvage_workflow.detecteer_winrar",
                      return_value=tool),
                patch("core.salvage_workflow.detecteer_7zip",
                      return_value=tool),
                patch("core.salvage_workflow.voer_winrar_recovery_uit") as wr,
                patch("core.salvage_workflow.salvage_extract") as extract,
                patch("core.salvage_workflow.vergelijk_extractie",
                      return_value=vergelijking),
            ):
                wr.return_value = SimpleNamespace(
                    status="PARTIAL", gekozen_archive=archive
                )
                extract.return_value = SimpleNamespace(
                    status="PARTIAL"
                )
                resultaat = voer_salvage_workflow_uit(
                    root, database_pad=db_pad, skip_par2=True
                )
            self.assertEqual(resultaat[0].eindstatus, "FAILED")
            wr.assert_called_once()
            extract.assert_called_once()

    def test_oude_rar_r00_set_wordt_gevonden(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            (root / "oud.rar").write_bytes(b"x")
            (root / "oud.r00").write_bytes(b"x")
            sets = ontdek_archive_sets(root)
            self.assertEqual(len(sets), 1)
            self.assertEqual(len(sets[0].volumes), 2)


if __name__ == "__main__":
    unittest.main()
