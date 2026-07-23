import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cli
from analyse import AnalyseResultaat, voer_analyse


class CliTest(unittest.TestCase):
    def test_help_is_nederlandstalig_en_toont_voorbeelden(self):
        uitvoer = io.StringIO()
        with contextlib.redirect_stdout(uitvoer):
            with self.assertRaises(SystemExit) as afsluiting:
                cli.main(["--help"])
        self.assertEqual(afsluiting.exception.code, 0)
        self.assertIn("--analyze", uitvoer.getvalue())
        self.assertIn("--extract", uitvoer.getvalue())
        self.assertIn("--repair", uitvoer.getvalue())
        self.assertIn("--spotify-search", uitvoer.getvalue())
        self.assertIn("--spotify-retry", uitvoer.getvalue())
        self.assertIn("Voorbeelden:", uitvoer.getvalue())
        self.assertIn("zonder bestanden te repareren", uitvoer.getvalue())

    def test_analyze_met_geldige_map(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            with patch("cli.voer_analyse") as analyse:
                code = cli.main(
                    ["--analyze", tijdelijke_map],
                    uitvoer=io.StringIO(),
                )
        self.assertEqual(code, 0)
        analyse.assert_called_once()
        self.assertEqual(analyse.call_args.args[0], Path(tijdelijke_map))
        self.assertEqual(analyse.call_args.args[1], Path(tijdelijke_map))

    def test_analyze_met_ongeldige_map(self):
        uitvoer = io.StringIO()
        code = cli.main(
            ["--analyze", r"Z:\bestaat\beslist\niet"],
            uitvoer=uitvoer,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("bestaat niet", uitvoer.getvalue())

    def test_starten_zonder_argumenten_blijft_interactief(self):
        antwoorden = iter([
            r"C:\demo\mp3",
            r"C:\demo\rar",
            "",
        ])
        vragen = []

        def invoer(vraag):
            vragen.append(vraag)
            return next(antwoorden)

        with patch("cli.voer_analyse") as analyse:
            code = cli.main([], invoer=invoer, uitvoer=io.StringIO())
        self.assertEqual(code, 0)
        self.assertIn(
            "Sleep de map met de UITGEPAKTE MP3's hierheen:",
            vragen[0],
        )
        analyse.assert_called_once_with(
            Path(r"C:\demo\mp3"),
            Path(r"C:\demo\rar"),
            uitvoer=unittest.mock.ANY,
        )

    def test_demo_gebruikt_bestaande_praktijktest(self):
        with patch(
            "tools.create_demo_recovery_test.voer_demo_uit"
        ) as demo:
            code = cli.main(["--demo"], uitvoer=io.StringIO())
        self.assertEqual(code, 0)
        demo.assert_called_once()

    def test_extract_gebruikt_aparte_schrijvende_workflow(self):
        overzicht = Mock(mislukt=0)
        with patch(
            "rar_extractor.voer_extractie_uit", return_value=overzicht
        ) as extractie:
            code = cli.main(
                ["--extract", r"C:\demo\download"],
                uitvoer=io.StringIO(),
            )
        self.assertEqual(code, 0)
        extractie.assert_called_once_with(
            Path(r"C:\demo\download"), uitvoer=unittest.mock.ANY
        )

    def test_extract_geeft_exitcode_een_bij_mislukking(self):
        overzicht = Mock(mislukt=1)
        with patch(
            "rar_extractor.voer_extractie_uit", return_value=overzicht
        ):
            code = cli.main(
                ["--extract", "."], uitvoer=io.StringIO()
            )
        self.assertEqual(code, 1)

    def test_repair_gebruikt_aparte_schrijvende_workflow(self):
        overzicht = Mock(mislukt=0)
        with patch(
            "par2_repair.voer_par2_reparatie_uit",
            return_value=overzicht,
        ) as reparatie:
            code = cli.main(
                ["--repair", r"C:\demo\download"],
                uitvoer=io.StringIO(),
            )
        self.assertEqual(code, 0)
        reparatie.assert_called_once_with(
            Path(r"C:\demo\download"), uitvoer=unittest.mock.ANY
        )

    def test_repair_accepteert_exact_een_mapargument(self):
        overzicht = Mock(mislukt=0)
        with patch(
            "par2_repair.voer_par2_reparatie_uit",
            return_value=overzicht,
        ) as reparatie:
            code = cli.main(
                ["--repair", "alleen-deze-map"],
                uitvoer=io.StringIO(),
            )
        self.assertEqual(code, 0)
        reparatie.assert_called_once_with(
            Path("alleen-deze-map"), uitvoer=unittest.mock.ANY
        )

        fouten = io.StringIO()
        with contextlib.redirect_stderr(fouten):
            with self.assertRaises(SystemExit) as afsluiting:
                cli.main(
                    ["--repair", "map-een", "map-twee"],
                    uitvoer=io.StringIO(),
                )
        self.assertEqual(afsluiting.exception.code, 2)
        self.assertIn("unrecognized arguments: map-twee", fouten.getvalue())

    def test_repair_zonder_mapargument_geeft_argparse_fout(self):
        fouten = io.StringIO()
        with contextlib.redirect_stderr(fouten):
            with self.assertRaises(SystemExit) as afsluiting:
                cli.main(["--repair"], uitvoer=io.StringIO())
        self.assertEqual(afsluiting.exception.code, 2)
        self.assertIn(
            "argument --repair: expected one argument",
            fouten.getvalue(),
        )

    def test_repair_met_niet_bestaande_map_geeft_geen_traceback(self):
        uitvoer = io.StringIO()
        code = cli.main(
            ["--repair", r"Z:\bestaat\beslist\niet"],
            uitvoer=uitvoer,
        )
        self.assertEqual(code, 1)
        self.assertIn("PAR2-map bestaat niet", uitvoer.getvalue())
        self.assertNotIn("Traceback", uitvoer.getvalue())

    def test_repair_workflow_is_geen_publieke_optie(self):
        helptekst = cli.maak_parser().format_help()
        self.assertNotIn("--repair-workflow", helptekst)

        fouten = io.StringIO()
        with contextlib.redirect_stderr(fouten):
            with self.assertRaises(SystemExit) as afsluiting:
                cli.main(
                    ["--repair-workflow", "map"],
                    uitvoer=io.StringIO(),
                )
        self.assertEqual(afsluiting.exception.code, 2)
        self.assertIn(
            "unrecognized arguments: --repair-workflow map",
            fouten.getvalue(),
        )

    def test_spotify_opties_accepteren_exact_een_map(self):
        overzicht = Mock(fouten=0)
        for optie, retry in (
            ("--spotify-search", False), ("--spotify-retry", True)
        ):
            with self.subTest(optie=optie):
                with patch(
                    "spotify_smart.voer_spotify_smart_uit",
                    return_value=overzicht,
                ) as zoeken:
                    code = cli.main(
                        [optie, "."], uitvoer=io.StringIO()
                    )
                self.assertEqual(code, 0)
                zoeken.assert_called_once_with(
                    Path("."), retry=retry, uitvoer=unittest.mock.ANY
                )
                fouten = io.StringIO()
                with contextlib.redirect_stderr(fouten):
                    with self.assertRaises(SystemExit):
                        cli.main([optie], uitvoer=io.StringIO())
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        cli.main(
                            [optie, ".", "extra"], uitvoer=io.StringIO()
                        )

    def test_spotify_niet_bestaande_map_geeft_geen_traceback(self):
        uitvoer = io.StringIO()
        code = cli.main(
            ["--spotify-search", r"Z:\bestaat\beslist\niet"],
            uitvoer=uitvoer,
        )
        self.assertEqual(code, 1)
        self.assertIn("Spotify-map bestaat niet", uitvoer.getvalue())
        self.assertNotIn("Traceback", uitvoer.getvalue())

    def test_ontbrekende_repair_tool_crasht_cli_niet(self):
        with patch(
            "par2_repair.voer_par2_reparatie_uit",
            side_effect=cli.Par2RepairFout("PAR2-tool niet gevonden"),
        ):
            uitvoer = io.StringIO()
            code = cli.main(
                ["--repair", "."], uitvoer=uitvoer
            )
        self.assertEqual(code, 1)
        self.assertIn("PAR2-tool niet gevonden", uitvoer.getvalue())

    def test_report_zonder_database(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            uitvoer = io.StringIO()
            cwd = Path.cwd()
            os.chdir(tijdelijke_map)
            try:
                code = cli.main(["--report"], uitvoer=uitvoer)
            finally:
                os.chdir(cwd)
        self.assertNotEqual(code, 0)
        self.assertIn("Geen normale database", uitvoer.getvalue())

    def test_analysefout_geeft_exitcode_een(self):
        with patch(
            "cli.voer_analyse",
            side_effect=cli.AnalyseFout("hulpprogramma ontbreekt"),
        ):
            uitvoer = io.StringIO()
            code = cli.main(
                ["--analyze", "."],
                uitvoer=uitvoer,
            )
        self.assertEqual(code, 1)
        self.assertIn("hulpprogramma ontbreekt", uitvoer.getvalue())


class AnalyseWorkflowTest(unittest.TestCase):
    def test_bestaande_volledige_workflow_wordt_hergebruikt(self):
        class FakeDatabase:
            def __len__(self):
                return 0

            def values(self):
                return []

            def sluit(self):
                self.gesloten = True

        database = FakeDatabase()
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            root = Path(tijdelijke_map)
            rapport = root / "rapport.txt"
            patches = [
                patch("analyse.maak_database", return_value=database),
                patch("analyse.zoek_mp3_bestanden", return_value=[]),
                patch("analyse.zoek_part01_bestanden", return_value=[]),
                patch("analyse.controleer_mp3_bestanden"),
                patch("analyse.voer_rar_inventory_uit"),
                patch("analyse.voer_par_inventory_uit"),
                patch("analyse.vergelijk_rar_inventory"),
                patch("analyse.voer_spotify_scan_uit"),
                patch("analyse.genereer_recovery_items"),
                patch("analyse.bepaal_recovery_identiteiten"),
                patch("analyse.voer_spotify_recovery_uit"),
                patch("analyse.maak_rapport", return_value=rapport),
            ]
            mocks = [context.start() for context in patches]
            try:
                resultaat = voer_analyse(
                    root,
                    root,
                    database_pad=root / "test.db",
                    uitvoer=io.StringIO(),
                )
            finally:
                for context in reversed(patches):
                    context.stop()

        self.assertIsInstance(resultaat, AnalyseResultaat)
        for mock in mocks[3:11]:
            mock.assert_called_once()
        self.assertTrue(database.gesloten)


if __name__ == "__main__":
    unittest.main()
