import contextlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QPushButton,
)

import cli
from core.progress import maak_progress
from gui import GuiDependencyFout
from gui.main_window import MegamanMainWindow, spotify_playlist_url


def wacht_tot(voorwaarde, timeout=3000):
    einde = time.monotonic() + timeout / 1000
    while time.monotonic() < einde:
        QApplication.processEvents()
        if voorwaarde():
            return True
        QTest.qWait(10)
    return voorwaarde()


def succesvolle_summary():
    return {
        "status": "geslaagd",
        "recovery_set": "Megaman2007",
        "total_mp3": 761,
        "ok": 737,
        "ffmpeg_errors": 19,
        "zero_byte": 5,
        "recovery_items": 24,
        "matched": 16,
        "low_confidence": 2,
        "manual_review": 0,
        "not_found": 6,
        "spotify_errors": 0,
        "playlist_added": 16,
        "playlist_existing": 0,
        "playlist_id": "playlist-id",
        "playlist_name": "Megaman2007",
        "report_path": "reports/rapport.txt",
    }


class SuccesWorkflow:
    threads = []
    runs = 0

    def __init__(self, delay=0):
        self.delay = delay

    def run(self, source, callbacks):
        type(self).threads.append(QThread.currentThread())
        type(self).runs += 1
        callbacks.stage_started("Analyse")
        callbacks.stage_progress(maak_progress(
            "Analyse", 396, 761,
            "MP3-bestanden controleren — bestand 396 van 761",
            ok_count=386,
            ffmpeg_error_count=10,
            zero_byte_count=0,
        ))
        if self.delay:
            time.sleep(self.delay)
        callbacks.stage_progress(maak_progress(
            "Analyse", 761, 761, "Analyse voltooid.",
            ok_count=737,
            ffmpeg_error_count=19,
            zero_byte_count=5,
        ))
        callbacks.stage_completed("Analyse")
        for stage in (
            "PAR2", "RAR Recovery", "Validatie", "Recovery Items",
            "Spotify Search", "Playlist Sync", "Rapport",
        ):
            callbacks.stage_started(stage)
            callbacks.stage_progress(maak_progress(
                stage, 1, 1, f"{stage} voltooid."
            ))
            callbacks.stage_completed(stage)
        callbacks.log_message("Workflowlog")
        callbacks.log_message("Workflowlog")
        return succesvolle_summary()


class FoutWorkflow:
    def run(self, source, callbacks):
        callbacks.stage_started("Analyse")
        raise RuntimeError("technische fout")


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
                    with self.assertRaises(SystemExit):
                        cli.main(
                            ["--gui", *actie], uitvoer=io.StringIO()
                        )
                self.assertIn(
                    "not allowed with argument", fouten.getvalue()
                )

    def test_ontbrekende_pyside_geeft_nette_opstartfout(self):
        uitvoer = io.StringIO()
        with patch(
            "gui.start_gui",
            side_effect=GuiDependencyFout("PySide6 ontbreekt."),
        ):
            code = cli.main(["--gui"], uitvoer=uitvoer)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", uitvoer.getvalue())

    def test_normale_gui_start_schrijft_niets_naar_console(self):
        uitvoer, fouten = io.StringIO(), io.StringIO()
        with (
            patch("gui.start_gui", return_value=0),
            contextlib.redirect_stdout(uitvoer),
            contextlib.redirect_stderr(fouten),
        ):
            code = cli.main(["--gui"], uitvoer=uitvoer)
        self.assertEqual(code, 0)
        self.assertEqual(uitvoer.getvalue(), "")
        self.assertEqual(fouten.getvalue(), "")


class MainWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        SuccesWorkflow.threads.clear()
        SuccesWorkflow.runs = 0
        self.temp = tempfile.TemporaryDirectory()
        self.venster = MegamanMainWindow(
            workflow_factory=SuccesWorkflow,
            set_name_resolver=lambda bron: "Megaman2007",
        )
        self.venster.map_invoer.setText(self.temp.name)

    def tearDown(self):
        if self.venster._thread_actief():
            self.assertTrue(wacht_tot(
                lambda: not self.venster._thread_actief(), 5000
            ))
        QCoreApplication.sendPostedEvents(
            None, QEvent.Type.DeferredDelete
        )
        QApplication.processEvents()
        self.venster.workflow_worker = None
        self.venster.workflow_thread = None
        self.venster.close()
        self.temp.cleanup()

    def test_layout_bevat_alleen_begeleide_workflow(self):
        teksten = {
            knop.text()
            for knop in self.venster.findChildren(QPushButton)
            if knop.isVisible() or self.venster.centralWidget()
        }
        self.assertEqual(
            teksten,
            {"Bladeren…", "Start", "Spotify-playlist openen"},
        )
        self.assertEqual(tuple(self.venster.stage_labels), (
            "Analyse", "PAR2", "RAR Recovery", "Validatie",
            "Recovery Items", "Spotify Search",
            "Playlist Sync", "Rapport",
        ))
        self.assertFalse(hasattr(self.venster, "spotify_tabel"))
        self.assertFalse(hasattr(self.venster, "analyseren_knop"))

    def test_ongeldige_map_start_niet(self):
        self.venster.map_invoer.clear()
        with patch.object(QMessageBox, "warning") as waarschuwing:
            self.venster._start_workflow()
        waarschuwing.assert_called_once()
        self.assertIsNone(self.venster.workflow_thread)

    def test_bronselectie_en_annuleren(self):
        bestaand = self.venster.map_invoer.text()
        with patch.object(
            QFileDialog, "getExistingDirectory", return_value=""
        ):
            self.venster._bladeren()
        self.assertEqual(self.venster.map_invoer.text(), bestaand)

        with tempfile.TemporaryDirectory() as gekozen:
            with patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=gekozen,
            ):
                self.venster._bladeren()
            self.assertEqual(self.venster.map_invoer.text(), gekozen)
            self.assertEqual(
                self.venster.recovery_set_label.text(), "Megaman2007"
            )

    def test_gestructureerde_progressie_en_reset(self):
        self.venster._stage_started("Validatie")
        self.venster._stage_progress(maak_progress(
            "Validatie", 396, 761, "bestand 396 van 761",
            ok_count=386,
            ffmpeg_error_count=10,
            zero_byte_count=0,
        ))
        self.assertGreater(self.venster.voortgang.value(), 0)
        self.assertEqual(
            self.venster.live_labels["files"].text(), "396 / 761"
        )
        self.assertEqual(self.venster.live_labels["ok"].text(), "386")
        self.assertEqual(
            self.venster.live_labels["ffmpeg"].text(), "10"
        )
        self.venster._reset_workflow()
        self.assertEqual(self.venster.voortgang.value(), 0)
        self.assertTrue(all(
            label.text() == "—"
            for label in self.venster.live_labels.values()
        ))

    def test_log_dedupliceert_en_begrenst(self):
        self.venster._append_log("dezelfde")
        self.venster._append_log("dezelfde")
        self.assertEqual(
            self.venster.logvenster.toPlainText().count("dezelfde"), 1
        )
        for nummer in range(1100):
            self.venster._append_log(f"regel {nummer}")
        self.assertLessEqual(
            self.venster.logvenster.document().blockCount(), 1000
        )

    def test_succes_stopt_thread_en_maakt_eindstatussen(self):
        with patch.object(QMessageBox, "critical") as foutdialoog:
            self.venster._start_workflow()
            thread = self.venster.workflow_thread
            worker = self.venster.workflow_worker
            self.assertIsNotNone(thread)
            self.assertIsNotNone(worker)
            self.assertFalse(self.venster.start_knop.isEnabled())
            self.assertTrue(wacht_tot(
                lambda: not self.venster._thread_actief()
            ))
        foutdialoog.assert_not_called()
        self.assertEqual(self.venster.thread_finished_count, 1)
        self.assertTrue(self.venster.start_knop.isEnabled())
        self.assertEqual(self.venster.voortgang.value(), 100)
        self.assertNotIn(
            "actief",
            " ".join(label.text() for label in self.venster.stage_labels.values()),
        )
        self.assertIn("MATCHED: 16", self.venster.eindoverzicht.text())
        self.assertTrue(self.venster.playlist_knop.isEnabled())
        self.assertIsNot(SuccesWorkflow.threads[0], self.app.thread())

    def test_fout_stopt_thread_en_reset_start(self):
        self.venster.workflow_factory = FoutWorkflow
        with patch.object(QMessageBox, "critical") as dialoog:
            self.venster._start_workflow()
            self.assertTrue(wacht_tot(
                lambda: not self.venster._thread_actief()
            ))
        dialoog.assert_called_once()
        self.assertTrue(self.venster.start_knop.isEnabled())
        self.assertEqual(self.venster.statusregel.text(), "Workflow mislukt.")
        self.assertIn(
            "technische fout", self.venster.logvenster.toPlainText()
        )

    def test_dubbele_start_wordt_voorkomen(self):
        self.venster.workflow_factory = (
            lambda: SuccesWorkflow(delay=0.2)
        )
        self.venster._start_workflow()
        eerste_thread = self.venster.workflow_thread
        self.venster._start_workflow()
        self.assertIs(self.venster.workflow_thread, eerste_thread)
        self.assertEqual(SuccesWorkflow.runs, 0)
        self.assertTrue(wacht_tot(
            lambda: not self.venster._thread_actief()
        ))
        self.assertEqual(SuccesWorkflow.runs, 1)

    def test_close_event_blokkeert_actieve_thread(self):
        self.venster.workflow_factory = (
            lambda: SuccesWorkflow(delay=0.2)
        )
        self.venster._start_workflow()
        event = QCloseEvent()
        with patch.object(QMessageBox, "information") as melding:
            self.venster.closeEvent(event)
        melding.assert_called_once()
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.venster._thread_actief())

    def test_playlist_url_wordt_met_qdesktopservices_geopend(self):
        self.assertEqual(
            spotify_playlist_url("abc"),
            "https://open.spotify.com/playlist/abc",
        )
        self.venster.playlist_id = "abc"
        with patch.object(QDesktopServices, "openUrl") as openen:
            self.venster._open_playlist()
        self.assertEqual(
            openen.call_args.args[0].toString(),
            "https://open.spotify.com/playlist/abc",
        )


if __name__ == "__main__":
    unittest.main()
