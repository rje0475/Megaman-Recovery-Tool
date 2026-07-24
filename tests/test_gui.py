import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

import cli
from gui import GuiDependencyFout
from gui.main_window import MegamanMainWindow
from gui.workers import ActionWorker


LEGE_STATISTIEKEN = {
    "mp3": 0,
    "rar": 0,
    "par2": 0,
    "complete": 0,
    "repairable": 0,
    "not_repairable": 0,
}


class CliGuiTest(unittest.TestCase):
    def test_gui_staat_in_helptekst(self):
        self.assertIn("--gui", cli.maak_parser().format_help())
        self.assertIn("python main.py --gui", cli.maak_parser().epilog)

    def test_gui_is_wederzijds_exclusief_met_alle_acties(self):
        for actie in (
            ["--analyze", "."],
            ["--repair", "."],
            ["--extract", "."],
            ["--demo"],
            ["--report"],
            ["--spotify-search", "."],
            ["--spotify-retry", "."],
        ):
            with self.subTest(actie=actie):
                fouten = io.StringIO()
                with contextlib.redirect_stderr(fouten):
                    with self.assertRaises(SystemExit) as afsluiting:
                        cli.main(
                            ["--gui", *actie], uitvoer=io.StringIO()
                        )
                self.assertEqual(afsluiting.exception.code, 2)
                self.assertIn("not allowed with argument", fouten.getvalue())

    def test_ontbrekende_pyside_geeft_nette_fout_zonder_traceback(self):
        uitvoer = io.StringIO()
        with patch(
            "gui.start_gui",
            side_effect=GuiDependencyFout("PySide6 ontbreekt."),
        ):
            code = cli.main(["--gui"], uitvoer=uitvoer)
        self.assertEqual(code, 1)
        self.assertIn("PySide6 ontbreekt", uitvoer.getvalue())
        self.assertNotIn("Traceback", uitvoer.getvalue())


class WorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_worker_geeft_log_voortgang_en_succes_door(self):
        gezien = {"log": [], "progress": [], "success": [], "completed": 0}

        def actie(uitvoer):
            uitvoer.write("werklog\n")
            return "klaar"

        worker = ActionWorker(actie)
        worker.log.connect(gezien["log"].append)
        worker.progress.connect(gezien["progress"].append)
        worker.succeeded.connect(gezien["success"].append)
        worker.completed.connect(
            lambda: gezien.__setitem__(
                "completed", gezien["completed"] + 1
            )
        )
        worker.run()

        self.assertEqual(gezien["progress"], [0, 100])
        self.assertEqual(gezien["log"], ["werklog\n"])
        self.assertEqual(gezien["success"], ["klaar"])
        self.assertEqual(gezien["completed"], 1)

    def test_worker_geeft_foutstatus_zonder_traceback_door(self):
        fouten = []
        worker = ActionWorker(
            lambda uitvoer: (_ for _ in ()).throw(ValueError("kapot"))
        )
        worker.failed.connect(fouten.append)
        worker.run()
        self.assertEqual(fouten, ["kapot"])
        self.assertNotIn("Traceback", fouten[0])


class _Hook:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in tuple(self.callbacks):
            callback(*args)


class _FakeWorker:
    instances = []
    start_callback = None

    def __init__(self, actie, *args):
        self.actie = actie
        self.args = args
        self.log = _Hook()
        self.progress = _Hook()
        self.succeeded = _Hook()
        self.failed = _Hook()
        self.completed = _Hook()
        self.__class__.instances.append(self)

    def start(self):
        if self.start_callback:
            type(self).start_callback(self)


class MainWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _FakeWorker.instances.clear()
        _FakeWorker.start_callback = None
        self.venster = MegamanMainWindow(
            worker_factory=_FakeWorker,
            statistics_reader=lambda: LEGE_STATISTIEKEN,
        )

    def tearDown(self):
        self.venster.close()

    def test_ongeldige_map_wordt_geweigerd(self):
        self.venster.map_invoer.setText(
            str(Path("bestaat-beslist-niet"))
        )
        with patch.object(QMessageBox, "warning") as waarschuwing:
            self.venster._analyseer()
        waarschuwing.assert_called_once()
        self.assertEqual(_FakeWorker.instances, [])

    def test_repair_vraagt_bevestiging(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            self.venster.map_invoer.setText(tijdelijke_map)
            with patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No,
            ) as vraag:
                self.venster._repareer()
        vraag.assert_called_once()
        self.assertEqual(_FakeWorker.instances, [])

    def test_extract_vraagt_bevestiging(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            self.venster.map_invoer.setText(tijdelijke_map)
            with patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No,
            ) as vraag:
                self.venster._pak_uit()
        vraag.assert_called_once()
        self.assertEqual(_FakeWorker.instances, [])

    def test_salvage_vraagt_bevestiging(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            self.venster.map_invoer.setText(tijdelijke_map)
            with patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No,
            ) as vraag:
                self.venster._salvage()
        vraag.assert_called_once()
        self.assertIn("Originele archieven", vraag.call_args.args[2])
        self.assertEqual(_FakeWorker.instances, [])

    def test_salvage_worker_krijgt_bronmap_en_aparte_workspace(self):
        with tempfile.TemporaryDirectory(
            prefix="4fe20a6a4f204822ed17e88d.#2."
        ) as tijdelijke_map:
            bronmap = Path(tijdelijke_map).resolve()
            self.venster.map_invoer.setText(str(bronmap))
            with patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                self.venster._salvage()
        self.assertEqual(len(_FakeWorker.instances), 1)
        worker = _FakeWorker.instances[0]
        self.assertEqual(worker.args[0], bronmap)
        self.assertEqual(worker.args[1], bronmap / "megaman_salvage")

    def test_knoppen_tijdens_actie_uit_en_daarna_aan(self):
        toestanden = []

        def start(worker):
            toestanden.append(all(
                not knop.isEnabled()
                for knop in self.venster.actieknoppen
            ))
            worker.log.emit("bezig\n")
            worker.progress.emit(50)
            worker.succeeded.emit("ok")
            worker.completed.emit()
            toestanden.append(all(
                knop.isEnabled()
                for knop in self.venster.actieknoppen
            ))

        _FakeWorker.start_callback = start
        self.venster._start_actie("Test", Mock())
        self.assertEqual(toestanden, [True, True])
        self.assertEqual(self.venster.voortgang.value(), 50)
        self.assertIn("bezig", self.venster.logvenster.toPlainText())

    def test_spotify_gedeelte_bevat_filters_en_geen_youtube(self):
        filters = [
            self.venster.spotify_filter.itemText(index)
            for index in range(self.venster.spotify_filter.count())
        ]
        self.assertEqual(filters, [
            "Alles", "FOUND", "AMBIGUOUS", "NOT_FOUND",
            "INSUFFICIENT_IDENTITY", "MANUAL", "REVIEWED_NONE",
        ])
        teksten = " ".join(
            knop.text() for knop in self.venster.findChildren(QPushButton)
        )
        self.assertIn("Spotify zoeken", teksten)
        self.assertNotIn("YouTube", teksten)


if __name__ == "__main__":
    unittest.main()
