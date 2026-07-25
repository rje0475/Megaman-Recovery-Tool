import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QPushButton,
)

import cli
from gui import GuiDependencyFout
from gui.main_window import MegamanMainWindow, spotify_playlist_url
from gui.workers import ActionWorker, WorkflowWorker
from core.salvage_workflow import voer_salvage_workflow_uit


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

    def test_workflowworker_draait_buiten_gui_thread(self):
        threads = []

        class Workflow:
            def run(self, source, callbacks):
                threads.append(QThread.currentThread())
                QThread.msleep(25)
                return {"status": "geslaagd"}

        worker = WorkflowWorker(Workflow(), Path("."))
        klaar = QSignalSpy(worker.workflow_completed)
        worker.start()
        if not klaar:
            self.assertTrue(klaar.wait(3000))
        worker.wait()
        self.assertIsNot(threads[0], self.app.thread())


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


class _FakeWorkflowWorker:
    instances = []
    start_callback = None

    def __init__(self, workflow, source):
        self.workflow = workflow
        self.source = source
        self.stage_started = _Hook()
        self.stage_progress = _Hook()
        self.stage_completed = _Hook()
        self.stage_skipped = _Hook()
        self.stage_failed = _Hook()
        self.log_message = _Hook()
        self.workflow_completed = _Hook()
        self.workflow_failed = _Hook()
        self.finished = _Hook()
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
        _FakeWorkflowWorker.instances.clear()
        _FakeWorkflowWorker.start_callback = None
        self.venster = MegamanMainWindow(
            worker_factory=_FakeWorker,
            workflow_worker_factory=_FakeWorkflowWorker,
            workflow_factory=lambda: Mock(name="workflow"),
            statistics_reader=lambda: LEGE_STATISTIEKEN,
        )

    def tearDown(self):
        self.venster.close()

    def test_hoofdvenster_bevat_centrale_workflow(self):
        self.assertEqual(
            self.venster.windowTitle(), "Megaman Recovery Tool"
        )
        self.assertTrue(self.venster.map_invoer.isReadOnly())
        self.assertEqual(
            tuple(self.venster.stage_labels),
            (
                "Analyse", "PAR2", "RAR Recovery", "Validatie",
                "Recovery Items", "Spotify Search",
                "Playlist Sync", "Rapport",
            ),
        )
        self.assertFalse(self.venster.playlist_knop.isEnabled())

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

    def test_workflow_start_valideert_bron_en_voorkomt_dubbele_start(self):
        with patch.object(QMessageBox, "warning") as waarschuwing:
            self.venster._start_workflow()
        waarschuwing.assert_called_once()
        self.assertEqual(_FakeWorkflowWorker.instances, [])

        with tempfile.TemporaryDirectory() as tijdelijke_map:
            self.venster.map_invoer.setText(tijdelijke_map)
            self.venster._start_workflow()
            self.venster._start_workflow()
        self.assertEqual(len(_FakeWorkflowWorker.instances), 1)
        self.assertFalse(self.venster.start_knop.isEnabled())

    def test_bronselectie_behoudt_waarde_bij_annuleren(self):
        self.venster.map_invoer.setText("bestaande-keuze")
        with patch.object(
            QFileDialog, "getExistingDirectory", return_value=""
        ):
            self.venster._bladeren()
        self.assertEqual(
            self.venster.map_invoer.text(), "bestaande-keuze"
        )

        with tempfile.TemporaryDirectory() as tijdelijke_map:
            with patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=tijdelijke_map,
            ):
                self.venster._bladeren()
            self.assertEqual(
                self.venster.map_invoer.text(), tijdelijke_map
            )

    def test_workflowsignalen_actualiseren_stages_log_en_overzicht(self):
        def start(worker):
            worker.stage_started.emit("Analyse")
            worker.stage_progress.emit(
                "Analyse", 1, 8, "Analyse bezig"
            )
            worker.log_message.emit("Een logregel")
            worker.stage_completed.emit("Analyse")
            worker.stage_skipped.emit(
                "Playlist Sync", "Spotify niet beschikbaar"
            )
            worker.workflow_completed.emit({
                "status": "gedeeltelijk geslaagd",
                "recovery_set": "Megaman2007",
                "recovery_items": 24,
                "matched": 16,
                "low_confidence": 2,
                "manual_review": 0,
                "not_found": 6,
                "playlist_added": 16,
                "playlist_existing": 0,
                "playlist_id": "playlist-id",
                "playlist_name": "Megaman2007",
            })
            worker.finished.emit()

        _FakeWorkflowWorker.start_callback = start
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            self.venster.map_invoer.setText(tijdelijke_map)
            self.venster._start_workflow()
        self.assertEqual(
            self.venster.stage_labels["Analyse"].property(
                "workflowState"
            ),
            "voltooid",
        )
        self.assertEqual(
            self.venster.stage_labels["Playlist Sync"].property(
                "workflowState"
            ),
            "overgeslagen",
        )
        self.assertIn(
            "Een logregel", self.venster.logvenster.toPlainText()
        )
        self.assertIn("MATCHED: 16", self.venster.eindoverzicht.text())
        self.assertTrue(self.venster.playlist_knop.isEnabled())
        self.assertFalse(self.venster.workflow_running)

    def test_workflowfout_blijft_zichtbaar_en_app_blijft_actief(self):
        def start(worker):
            worker.stage_started.emit("Analyse")
            worker.stage_failed.emit("Analyse", "technische fout")
            worker.log_message.emit("Traceback: technische details")
            worker.workflow_failed.emit("leesbare fout")
            worker.finished.emit()

        _FakeWorkflowWorker.start_callback = start
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            self.venster.map_invoer.setText(tijdelijke_map)
            with patch.object(QMessageBox, "critical") as foutdialoog:
                self.venster._start_workflow()
        foutdialoog.assert_called_once()
        self.assertIn("mislukt", self.venster.statusregel.text())
        self.assertIn(
            "technische details",
            self.venster.logvenster.toPlainText(),
        )
        self.assertTrue(self.venster.start_knop.isEnabled())

    def test_playlist_url_en_openknop(self):
        self.assertEqual(
            spotify_playlist_url("abc123"),
            "https://open.spotify.com/playlist/abc123",
        )
        self.venster.playlist_id = "abc123"
        with patch.object(QDesktopServices, "openUrl") as openen:
            self.venster._open_playlist()
        self.assertEqual(
            openen.call_args.args[0].toString(),
            "https://open.spotify.com/playlist/abc123",
        )

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
        self.assertIs(worker.actie, voer_salvage_workflow_uit)
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
