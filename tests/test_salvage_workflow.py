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
from core.salvage_classification import classificeer_salvage_resultaat
from core.salvage_extractor import salvage_extract, winrar_salvage_extract
from core.salvage_workflow import (
    ArchiveSet,
    SalvageFout,
    _extractiefout_categorie,
    _resolveer_set_volumes,
    _synchroniseer_recovery,
    ontdek_archive_sets,
    voer_salvage_workflow_uit,
)
from core.winrar_recovery import (
    classificeer_archive_set,
    vind_herstelde_volumes,
    vind_herstelde_sets,
    voer_winrar_recovery_uit,
)
from database import (
    bewaar_rar_set,
    maak_database,
    vervang_rar_inventory_items,
)


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
        commando = []
        opties = {}

        def runner(command, **kwargs):
            commando.extend(command)
            opties.update(kwargs)
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
        self.assertIn("-ibck", commando)
        self.assertIn("-inul", commando)
        self.assertIn("-y", commando)
        self.assertIs(opties["shell"], False)

    def test_console_rar_is_stil_zonder_gui_switch(self):
        rar = self.root / "Rar.exe"
        rar.write_bytes(b"exe")
        tool = ToolResultaat("RAR/WinRAR", rar, True, "TEST")
        gezien = []

        def runner(command, **kwargs):
            gezien.extend(command)
            Path(kwargs["cwd"], "rebuilt_set met spaties.part01.rar").write_bytes(
                b"x"
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        voer_winrar_recovery_uit(
            (self.archive,), self.root / "rar-workspace", tool, runner
        )
        self.assertEqual(gezien[1:4], ["r", "-inul", "-y"])
        self.assertNotIn("-ibck", gezien)

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

    def test_rebuilt_multipart_is_generiek_en_numeriek_gesorteerd(self):
        workspace = self.root / "rebuilt"
        workspace.mkdir()
        for naam in (
            "rebuilt.Andere Naam.part10.rar",
            "rebuilt.Andere Naam.part2.rar",
            "rebuilt.Andere Naam.part1.rar",
            "rebuilt.Andere Naam.part7.old",
        ):
            (workspace / naam).write_bytes(b"x")
        self.assertEqual(
            [pad.name for pad in vind_herstelde_volumes(workspace)],
            [
                "rebuilt.Andere Naam.part1.rar",
                "rebuilt.Andere Naam.part2.rar",
                "rebuilt.Andere Naam.part10.rar",
            ],
        )

    def test_enkel_rebuilt_volume_tegenover_grote_bronset_is_single_volume(self):
        workspace = self.root / "classificatie"
        workspace.mkdir()
        rebuilt = workspace / "rebuilt.Willekeurige Set.part01.rar"
        rebuilt.write_bytes(b"x")
        resultaat = vind_herstelde_sets(workspace, 82)
        self.assertEqual(len(resultaat), 1)
        self.assertEqual(resultaat[0].classificatie, "SINGLE_VOLUME")
        self.assertEqual(resultaat[0].ontbrekende_delen[0], 2)
        self.assertEqual(resultaat[0].ontbrekende_delen[-1], 82)

    def test_complete_rebuilt_set_wordt_herkend(self):
        workspace = self.root / "compleet"
        workspace.mkdir()
        for nummer in range(1, 5):
            (workspace / f"repaired_Andere Set.part{nummer:03d}.rar").write_bytes(
                b"x"
            )
        resultaat = vind_herstelde_sets(workspace, 4)
        self.assertEqual(resultaat[0].classificatie, "COMPLETE")
        self.assertEqual(len(resultaat[0].volumes), 4)

    def test_ontbrekend_partnummer_wordt_gerapporteerd(self):
        volumes = (
            self.root / "Losse Set.part1.rar",
            self.root / "Losse Set.part3.rar",
        )
        for volume in volumes:
            volume.write_bytes(b"x")
        resultaat = classificeer_archive_set(volumes, 3, "repaired")
        self.assertEqual(resultaat.classificatie, "PARTIAL")
        self.assertEqual(resultaat.ontbrekende_delen, (2,))
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

    def test_winrar_partial_daarna_7zip_behoudt_eerdere_bestanden(self):
        winrar = self.root / "WinRAR.exe"
        winrar.write_bytes(b"exe")
        winrar_tool = ToolResultaat("RAR/WinRAR", winrar, True, "TEST")
        doel = self.root / "samengevoegd"

        def winrar_runner(command, **kwargs):
            (doel / "Week01").mkdir(parents=True)
            (doel / "Week01" / "eerste.mp3").write_bytes(b"eerste")
            return SimpleNamespace(
                returncode=1, stdout="", stderr="CRC error"
            )

        eerste = winrar_salvage_extract(
            self.archive, doel, winrar_tool, winrar_runner
        )

        def zeven_runner(command, **kwargs):
            (doel / "Week02").mkdir(parents=True)
            (doel / "Week02" / "tweede.mp3").write_bytes(b"tweede")
            return SimpleNamespace(
                returncode=2, stdout="", stderr="ERROR: CRC Failed"
            )

        tweede = salvage_extract(
            self.archive, doel, self.tool, zeven_runner
        )
        self.assertEqual(eerste.status, "PARTIAL")
        self.assertEqual(tweede.status, "PARTIAL")
        self.assertTrue((doel / "Week01" / "eerste.mp3").is_file())
        self.assertTrue((doel / "Week02" / "tweede.mp3").is_file())
        self.assertIn("-o-", eerste.commando)
        self.assertIn("-aos", tweede.commando)

    def test_tooluitvoer_bepaalt_foutcategorie_niet_exitcode_alleen(self):
        basis = {"exitcode": 6, "stdout": "", "foutmelding": None}
        ontbrekend = SimpleNamespace(
            **basis, stderr="Cannot find volume collection.part02.rar"
        )
        niet_open = SimpleNamespace(
            **basis, stderr="Cannot open collection.part01.rar"
        )
        crc = SimpleNamespace(**basis, stderr="CRC error in track.mp3")
        ander = SimpleNamespace(**basis, stderr="Unknown fatal error")
        self.assertEqual(
            _extractiefout_categorie(ontbrekend),
            "ONTBREKEND_VERVOLGVOLUME",
        )
        self.assertEqual(
            _extractiefout_categorie(niet_open), "BRON_NIET_GEOPEND"
        )
        self.assertEqual(
            _extractiefout_categorie(crc), "CRC_OF_DATAFOUT"
        )
        self.assertEqual(
            _extractiefout_categorie(ander), "ANDERE_TOOLFOUT"
        )


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


class DefinitieveClassificatieTest(unittest.TestCase):
    @staticmethod
    def _rij(pad, ffmpeg=False, nul=False, bestand=None):
        return {
            "relatief_pad": pad,
            "bestand": bestand or pad,
            "nul_bytes": nul,
            "ffmpeg_status": "ERROR" if ffmpeg else "OK",
            "ffmpeg_type": "Header missing" if ffmpeg else None,
            "ffmpeg_melding": "header ontbreekt" if ffmpeg else None,
        }

    def test_761_aanwezig_met_19_ffmpeg_en_5_nul_levert_24_items(self):
        items = tuple(
            VergelijkItem(
                fr"Collectie\track{nummer:03d}.mp3", "OK",
                Path(f"track{nummer:03d}.mp3"), 3, 3, "ok",
            )
            for nummer in range(761)
        )
        analyse = [
            self._rij(
                fr"scan\Collectie\track{nummer:03d}.mp3",
                ffmpeg=True,
                bestand=Path("C:/bron/scan/Collectie")
                / f"track{nummer:03d}.mp3",
            )
            for nummer in range(19)
        ] + [
            self._rij(fr"Collectie\track{nummer:03d}.mp3", nul=True)
            for nummer in range(19, 24)
        ]
        resultaat = classificeer_salvage_resultaat(
            VergelijkResultaat(items, ()), analyse,
            wortels=(Path("C:/bron"),),
        )
        self.assertEqual(resultaat.verwacht, 761)
        self.assertEqual(resultaat.fysiek_aanwezig, 761)
        self.assertEqual(resultaat.volledig_goed, 737)
        self.assertEqual(resultaat.beschadigd_aanwezig, 24)
        self.assertEqual(resultaat.onleesbaar, 19)
        self.assertEqual(resultaat.nul_bytes, 5)
        self.assertEqual(resultaat.ontbrekend, 0)
        self.assertEqual(resultaat.recovery_items, 24)

    def test_overlap_ffmpeg_nul_case_en_absolute_pad_wordt_een_item(self):
        intern = "Jaar\\Één Song's.mp3"
        vergelijking = VergelijkResultaat((
            VergelijkItem(
                intern, "ZERO_BYTE", Path("uit/Jaar/Één Song's.mp3"),
                1, 0, "zero_byte_after_salvage",
            ),
        ), ())
        analyse = [self._rij(
            r"JAAR\ÉÉN SONG'S.MP3", ffmpeg=True, nul=True,
            bestand=Path("C:/bron/Jaar/Één Song's.mp3"),
        )]
        resultaat = classificeer_salvage_resultaat(
            vergelijking, analyse, wortels=(Path("C:/bron"),)
        )
        self.assertEqual(resultaat.recovery_items, 1)
        self.assertEqual(resultaat.nul_bytes, 1)
        self.assertGreaterEqual(resultaat.duplicaten_verwijderd, 1)
        item = resultaat.vergelijking.items[0]
        self.assertEqual(item.status, "ZERO_BYTE")
        self.assertIn("Header missing", item.ffmpeg_fout)

    def test_lege_inventaris_blijft_leeg(self):
        resultaat = classificeer_salvage_resultaat(
            VergelijkResultaat((), ()),
            [self._rij("los.mp3", ffmpeg=True)],
        )
        self.assertEqual(resultaat.verwacht, 0)
        self.assertEqual(resultaat.recovery_items, 0)
        self.assertEqual(resultaat.fysiek_aanwezig, 0)


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

    def test_database_gebruikt_dezelfde_ffmpeg_deduplicatie(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            db = maak_database(Path(tijdelijke_map) / "test.db")
            try:
                vergelijking = VergelijkResultaat((
                    VergelijkItem(
                        "Jaar/Defect.mp3", "ZERO_BYTE", Path("defect.mp3"),
                        1, 0, "zero_byte_after_salvage",
                    ),
                ), ())
                classificatie = classificeer_salvage_resultaat(
                    vergelijking,
                    [DefinitieveClassificatieTest._rij(
                        r"JAAR\DEFECT.MP3", ffmpeg=True, nul=True
                    )],
                )
                aantal = _synchroniseer_recovery(
                    db, "set", classificatie.vergelijking
                )
                rijen = db.verbinding.execute(
                    "SELECT * FROM recovery_items"
                ).fetchall()
                self.assertEqual(aantal, 1)
                self.assertEqual(len(rijen), 1)
                self.assertEqual(rijen[0]["feit_nul_bytes"], 1)
                self.assertEqual(rijen[0]["feit_corrupt"], 1)
                self.assertEqual(rijen[0]["probleem_type"], "corrupt")
                self.assertIn("Header missing", rijen[0]["ffmpeg_fout"])
                self.assertIn("ffmpeg", rijen[0]["probleem_bron"])
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
                patch("core.salvage_workflow.winrar_salvage_extract") as wx,
                patch("core.salvage_workflow.salvage_extract") as extract,
                patch("core.salvage_workflow.vergelijk_extractie",
                      return_value=vergelijking),
            ):
                wr.return_value = SimpleNamespace(
                    status="PARTIAL", gekozen_archive=archive,
                    exitcode=1, commando=("rar.exe", "r"),
                    herstelde_volumes=(),
                )
                wx.return_value = SimpleNamespace(
                    status="PARTIAL", exitcode=1
                )
                extract.return_value = SimpleNamespace(
                    status="PARTIAL", exitcode=2, data_fouten=("CRC",)
                )
                resultaat = voer_salvage_workflow_uit(
                    root, database_pad=db_pad, skip_par2=True
                )
            self.assertEqual(resultaat[0].eindstatus, "FAILED")
            wr.assert_called_once()
            wx.assert_called_once()
            extract.assert_called_once()

    def test_rebuilt_wordt_gekozen_beide_extracties_draaien_en_rescan_bepaalt_items(
        self,
    ):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            originele_volumes = []
            for nummer in range(1, 83):
                volume = root / f"Willekeurige Collectie.part{nummer:02d}.rar"
                volume.write_bytes(b"rar")
                originele_volumes.append(volume)
            archive = originele_volumes[0]
            rebuilt = root / "rebuilt.Willekeurige Collectie.part01.rar"
            rebuilt.write_bytes(b"repaired")
            db_pad = root / "test.db"
            db = maak_database(db_pad)
            bewaar_rar_set(db, "willekeurige collectie", archive, True)
            vervang_rar_inventory_items(
                db, "willekeurige collectie", archive, [
                    {
                        "verwacht_rel_pad": r"Week09\rebuilt.mp3",
                        "verwacht_rel_pad_norm": r"week09\rebuilt.mp3",
                        "verwachte_map": "Week09",
                        "verwachte_bestandsnaam": "rebuilt.mp3",
                        "verwachte_grootte": 3,
                    },
                    {
                        "verwacht_rel_pad": r"Week10\original.mp3",
                        "verwacht_rel_pad_norm": r"week10\original.mp3",
                        "verwachte_map": "Week10",
                        "verwachte_bestandsnaam": "original.mp3",
                        "verwachte_grootte": 3,
                    },
                ]
            )
            db.verbinding.execute(
                """
                INSERT INTO par_inventory (
                  par_set_key, gekoppelde_rar_set_key, par_startbestand,
                  status, bijgewerkt_op
                ) VALUES (?, ?, ?, 'NOT_REPAIRABLE', 'nu')
                """,
                ("willekeurige collectie", "willekeurige collectie", "set.par2"),
            )
            db.verbinding.commit()
            db.sluit()
            tool_pad = root / "tool.exe"
            tool_pad.write_bytes(b"exe")
            tool = ToolResultaat("test", tool_pad, True, "TEST")
            fasen = []

            def winrar_extractie(bron, doel, **kwargs):
                fasen.append(("winrar", Path(bron)))
                rebuilt_bron = Path(bron).name.casefold().startswith("rebuilt")
                map_ = Path(doel) / ("Week09" if rebuilt_bron else "Week10")
                map_.mkdir(parents=True, exist_ok=True)
                naam = "rebuilt.mp3" if rebuilt_bron else "original.mp3"
                (map_ / naam).write_bytes(b"mp3")
                return SimpleNamespace(status="PARTIAL", exitcode=1)

            def zeven_extractie(bron, doel, **kwargs):
                fasen.append(("7zip", Path(bron)))
                self.assertTrue(
                    (Path(doel) / "Week09" / "rebuilt.mp3").is_file()
                )
                rebuilt_bron = Path(bron).name.casefold().startswith("rebuilt")
                return SimpleNamespace(
                    status="SUCCESS" if rebuilt_bron else "PARTIAL",
                    exitcode=0 if rebuilt_bron else 2,
                    data_fouten=() if rebuilt_bron else ("CRC",),
                )

            def echte_vergelijking(verwacht, doel):
                fasen.append(("rescan", Path(doel)))
                return vergelijk_extractie(
                    verwacht, doel, mp3_lezer=lambda _: True
                )

            log = StringIO()
            with (
                patch("core.salvage_workflow.detecteer_winrar",
                      return_value=tool),
                patch("core.salvage_workflow.detecteer_7zip",
                      return_value=tool),
                patch(
                    "core.salvage_workflow.voer_winrar_recovery_uit",
                    return_value=SimpleNamespace(
                        status="PARTIAL", gekozen_archive=rebuilt,
                        exitcode=3, commando=("rar.exe", "r", "-inul", "-y"),
                        herstelde_volumes=(rebuilt,),
                    ),
                ),
                patch(
                    "core.salvage_workflow.winrar_salvage_extract",
                    side_effect=winrar_extractie,
                ),
                patch(
                    "core.salvage_workflow.salvage_extract",
                    side_effect=zeven_extractie,
                ),
                patch(
                    "core.salvage_workflow.vergelijk_extractie",
                    side_effect=echte_vergelijking,
                ),
            ):
                resultaat = voer_salvage_workflow_uit(
                    root, database_pad=db_pad, skip_par2=True, uitvoer=log
                )
            self.assertEqual(
                fasen,
                [
                    ("winrar", rebuilt),
                    ("7zip", rebuilt),
                    ("winrar", archive),
                    ("7zip", archive),
                    ("rescan", root / "megaman_salvage"
                     / "willekeurige_collectie" / "extracted"),
                ],
            )
            self.assertEqual(resultaat[0].eindstatus, "SALVAGED")
            self.assertEqual(resultaat[0].spotify_recovery_items, 0)
            tekst = log.getvalue()
            for fase in (
                "Salvage-workflow gestart",
                "WinRAR recovery exitcode: 3",
                "Salvagebron: rebuilt",
                "Salvagebron: origineel",
                "Classificatie bron: SINGLE_VOLUME",
                "Classificatie bron: COMPLETE",
                "Ontbrekende volumes: 2, 3",
                "Tool: RAR/WinRAR",
                "Tool: 7-Zip",
                "Nieuw teruggewonnen:",
                "Extracted-map opnieuw gescand",
                "Fysiek aanwezig: 2",
                "Volledig goed: 2",
                "FFmpeg-fouten ingelezen: 0",
                "Duplicaten verwijderd: 0",
                "Definitieve recovery-items: 0",
            ):
                self.assertIn(fase, tekst)

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
