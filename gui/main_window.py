"""Opgeruimd hoofdvenster voor één begeleide recoveryworkflow."""

import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QThread, QUrl, Qt
from PySide6.QtGui import QCloseEvent, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.workflow import (
    RecoveryGuiWorkflow,
    WORKFLOW_STAGES,
    bepaal_recovery_setnaam,
)
from gui.recovery_review import RecoveryReviewDialog
from gui.workers import WorkflowWorker


STAGE_SYMBOLS = {
    "wachtend": "○",
    "actief": "▶",
    "voltooid": "✓",
    "overgeslagen": "—",
    "mislukt": "✗",
}
_GEVOELIGE_LOGREGEL = re.compile(
    r"(?i)(accounts\.spotify\.com/authorize|"
    r"client[_ -]?secret|access[_ -]?token|refresh[_ -]?token|"
    r"authorization[_ -]?code)"
)


def spotify_playlist_url(playlist_id):
    playlist_id = str(playlist_id or "").strip()
    return (
        f"https://open.spotify.com/playlist/{playlist_id}"
        if playlist_id else None
    )


class MegamanMainWindow(QMainWindow):
    def __init__(
        self,
        workflow_factory=RecoveryGuiWorkflow,
        worker_factory=WorkflowWorker,
        thread_factory=QThread,
        set_name_resolver=bepaal_recovery_setnaam,
        review_factory=RecoveryReviewDialog,
    ):
        super().__init__()
        self.workflow_factory = workflow_factory
        self.worker_factory = worker_factory
        self.thread_factory = thread_factory
        self.set_name_resolver = set_name_resolver
        self.review_factory = review_factory
        self.workflow_thread = None
        self.workflow_worker = None
        self.workflow_running = False
        self.thread_finished_count = 0
        self._worker_done = False
        self._thread_done = False
        self.playlist_id = None
        self.review_dialog = None
        self.review_active = False
        self._last_summary = None
        self._last_log = None
        self._progress_timer = QElapsedTimer()
        self._last_progress_percent = -1
        self.setWindowTitle("Megaman Recovery Tool")
        self.resize(860, 720)
        self._bouw_interface()
        self._reset_workflow()

    def _bouw_interface(self):
        centraal = QWidget()
        layout = QVBoxLayout(centraal)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Muziekmap:"))
        bronrij = QHBoxLayout()
        self.map_invoer = QLineEdit()
        self.map_invoer.setReadOnly(True)
        self.map_invoer.setPlaceholderText(
            "Selecteer een bronmap met RAR/PAR2-bestanden"
        )
        self.bladeren_knop = QPushButton("Bladeren…")
        self.bladeren_knop.clicked.connect(self._bladeren)
        bronrij.addWidget(self.map_invoer, stretch=1)
        bronrij.addWidget(self.bladeren_knop)
        layout.addLayout(bronrij)

        setrij = QHBoxLayout()
        setrij.addWidget(QLabel("Recovery-set:"))
        self.recovery_set_label = QLabel("Nog niet bepaald")
        self.recovery_set_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        setrij.addWidget(self.recovery_set_label, stretch=1)
        layout.addLayout(setrij)

        layout.addWidget(QLabel("Workflow"))
        stappen = QGridLayout()
        stappen.setHorizontalSpacing(24)
        self.stage_labels = {}
        for index, stage in enumerate(WORKFLOW_STAGES):
            label = QLabel()
            self.stage_labels[stage] = label
            stappen.addWidget(label, index // 2, index % 2)
        layout.addLayout(stappen)

        fasegrid = QGridLayout()
        fasegrid.addWidget(QLabel("Huidige stap:"), 0, 0)
        self.huidige_stap_label = QLabel("Geen")
        fasegrid.addWidget(self.huidige_stap_label, 0, 1)
        fasegrid.addWidget(QLabel("Huidige activiteit:"), 1, 0)
        self.huidige_activiteit_label = QLabel("Wachten op Start")
        self.huidige_activiteit_label.setWordWrap(True)
        fasegrid.addWidget(self.huidige_activiteit_label, 1, 1)
        layout.addLayout(fasegrid)

        self.voortgang = QProgressBar()
        self.voortgang.setRange(0, 100)
        self.voortgang.setFormat("%p%")
        layout.addWidget(self.voortgang)

        statistieken = QGridLayout()
        self.live_labels = {}
        velden = (
            ("files", "Bestanden"),
            ("ok", "OK"),
            ("ffmpeg", "FFmpeg-fouten"),
            ("zero", "0-byte"),
            ("spotify", "Spotify verwerkt"),
            ("matched", "MATCHED"),
            ("low", "LOW_CONFIDENCE"),
            ("manual", "MANUAL_REVIEW"),
            ("not_found", "NOT_FOUND"),
            ("errors", "Technische fouten"),
        )
        for index, (key, tekst) in enumerate(velden):
            rij, kolom = divmod(index, 5)
            blok = QHBoxLayout()
            blok.addWidget(QLabel(f"{tekst}:"))
            waarde = QLabel("—")
            waarde.setObjectName(f"live_{key}")
            blok.addWidget(waarde)
            blok.addStretch(1)
            statistieken.addLayout(blok, rij, kolom)
            self.live_labels[key] = waarde
        layout.addLayout(statistieken)

        layout.addWidget(QLabel("Log"))
        self.logvenster = QPlainTextEdit()
        self.logvenster.setReadOnly(True)
        self.logvenster.document().setMaximumBlockCount(1000)
        layout.addWidget(self.logvenster, stretch=1)

        self.eindoverzicht = QLabel(
            "Eindoverzicht verschijnt na de workflow."
        )
        self.eindoverzicht.setWordWrap(True)
        self.eindoverzicht.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.eindoverzicht)

        knoppen = QHBoxLayout()
        knoppen.addStretch(1)
        self.start_knop = QPushButton("Start")
        self.start_knop.clicked.connect(self._start_workflow)
        self.playlist_knop = QPushButton("Spotify-playlist openen")
        self.playlist_knop.setEnabled(False)
        self.playlist_knop.clicked.connect(self._open_playlist)
        knoppen.addWidget(self.start_knop)
        knoppen.addWidget(self.playlist_knop)
        knoppen.addStretch(1)
        layout.addLayout(knoppen)

        self.statusregel = QLabel("Gereed")
        layout.addWidget(self.statusregel)
        self.setCentralWidget(centraal)

    def _bladeren(self):
        gekozen = QFileDialog.getExistingDirectory(
            self, "Selecteer een bronmap", self.map_invoer.text()
        )
        if not gekozen:
            return
        self.map_invoer.setText(gekozen)
        try:
            setnaam = self.set_name_resolver(Path(gekozen))
        except Exception:
            setnaam = Path(gekozen).name
        self.recovery_set_label.setText(setnaam)

    def _geldige_bron(self):
        tekst = self.map_invoer.text().strip().strip('"')
        bron = Path(tekst) if tekst else None
        if bron is None or not bron.is_dir():
            QMessageBox.warning(
                self,
                "Ongeldige map",
                "Selecteer eerst een bestaande bronmap.",
            )
            return None
        return bron.resolve()

    def _thread_actief(self):
        return self.workflow_running or self.review_active

    def _start_workflow(self):
        if self._thread_actief():
            self._append_log(
                "Er draait al een workflow; dubbele start genegeerd."
            )
            return
        bron = self._geldige_bron()
        if bron is None:
            return
        self._reset_workflow()
        self.map_invoer.setText(str(bron))
        self.recovery_set_label.setText(
            self.set_name_resolver(bron)
        )
        self.statusregel.setText("Workflow wordt uitgevoerd…")
        self.huidige_activiteit_label.setText("Workflow voorbereiden")
        self.start_knop.setEnabled(False)
        self.bladeren_knop.setEnabled(False)

        thread = self.thread_factory(self)
        thread.setObjectName("MegamanRecoveryWorkflowThread")
        worker = self.worker_factory(self.workflow_factory(), bron)
        worker.setObjectName("MegamanRecoveryWorkflowWorker")
        worker.moveToThread(thread)
        self.workflow_thread = thread
        self.workflow_worker = worker
        self.workflow_running = True
        self._worker_done = False
        self._thread_done = False

        thread.started.connect(worker.run)
        worker.stage_started.connect(self._stage_started)
        worker.stage_progress.connect(self._stage_progress)
        worker.stage_completed.connect(self._stage_completed)
        worker.stage_skipped.connect(self._stage_skipped)
        worker.stage_failed.connect(self._stage_failed)
        worker.log_message.connect(self._append_log)
        worker.workflow_completed.connect(self._workflow_completed)
        worker.workflow_failed.connect(self._workflow_failed)
        worker.review_requested.connect(self._open_recovery_review)
        worker.finished.connect(self._worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _reset_workflow(self):
        self.playlist_id = None
        self._last_summary = None
        self.playlist_knop.setEnabled(False)
        self.voortgang.setValue(0)
        self.huidige_stap_label.setText("Geen")
        self.huidige_activiteit_label.setText("Wachten op Start")
        self.eindoverzicht.setText(
            "Eindoverzicht verschijnt na de workflow."
        )
        for stage in WORKFLOW_STAGES:
            self._set_stage_state(stage, "wachtend")
        for label in self.live_labels.values():
            label.setText("—")
        self._last_progress_percent = -1
        self._progress_timer.restart()

    def _set_stage_state(self, stage, state, detail=None):
        label = self.stage_labels[stage]
        tekst = f"{STAGE_SYMBOLS[state]} {stage} — {state}"
        if detail:
            tekst += f": {detail}"
        label.setText(tekst)
        label.setProperty("workflowState", state)

    def _stage_started(self, stage):
        actief = [
            naam for naam, label in self.stage_labels.items()
            if label.property("workflowState") == "actief"
            and naam != stage
        ]
        for naam in actief:
            self._set_stage_state(
                naam, "mislukt", "Faseovergang niet afgerond."
            )
        self._set_stage_state(stage, "actief")
        self.huidige_stap_label.setText(stage)
        self.huidige_activiteit_label.setText(f"{stage} gestart")
        self.statusregel.setText(f"Bezig: {stage}")
        self._append_log(f"{stage} gestart.")

    def _stage_progress(self, progress):
        eindupdate = (
            progress.total > 0
            and progress.current >= progress.total
        )
        if (
            not eindupdate
            and progress.percent == self._last_progress_percent
            and self._progress_timer.isValid()
            and self._progress_timer.elapsed() < 75
        ):
            return
        self._last_progress_percent = progress.percent
        self._progress_timer.restart()
        index = WORKFLOW_STAGES.index(progress.stage)
        overall = round(
            100 * (index + progress.percent / 100) / len(WORKFLOW_STAGES)
        )
        self.voortgang.setValue(max(self.voortgang.value(), overall))
        self.huidige_activiteit_label.setText(
            progress.message or progress.stage
        )
        if progress.total:
            sleutel = (
                "spotify"
                if progress.stage == "Spotify Search" else "files"
            )
            self.live_labels[sleutel].setText(
                f"{progress.current} / {progress.total}"
            )
        mappings = {
            "ok": progress.ok_count,
            "ffmpeg": progress.ffmpeg_error_count,
            "zero": progress.zero_byte_count,
            "matched": progress.matched_count,
            "low": progress.low_confidence_count,
            "manual": progress.manual_review_count,
            "not_found": progress.not_found_count,
            "errors": progress.error_count,
        }
        for key, waarde in mappings.items():
            if waarde is not None:
                self.live_labels[key].setText(str(waarde))

    def _stage_completed(self, stage):
        self._set_stage_state(stage, "voltooid")
        self._append_log(f"{stage} voltooid.")

    def _stage_skipped(self, stage, reason):
        self._set_stage_state(stage, "overgeslagen", reason)
        self._append_log(f"{stage} overgeslagen: {reason}")

    def _stage_failed(self, stage, error):
        self._set_stage_state(stage, "mislukt", error)
        self._append_log(f"{stage} mislukt: {error}")

    def _append_log(self, message):
        for regel in str(message or "").splitlines():
            regel = regel.strip()
            if not regel or _GEVOELIGE_LOGREGEL.search(regel):
                continue
            if regel == self._last_log:
                continue
            self._last_log = regel
            tijd = datetime.now().strftime("%H:%M:%S")
            self.logvenster.appendPlainText(f"[{tijd}] {regel}")
        cursor = self.logvenster.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.logvenster.setTextCursor(cursor)
        self.logvenster.ensureCursorVisible()

    def _workflow_completed(self, summary):
        for stage, label in self.stage_labels.items():
            if label.property("workflowState") in {"wachtend", "actief"}:
                self._set_stage_state(
                    stage, "overgeslagen",
                    "Geen afzonderlijke actie nodig.",
                )
        self.voortgang.setValue(100)
        self.huidige_stap_label.setText("Voltooid")
        self.huidige_activiteit_label.setText(
            "Alle workflowfasen zijn afgerond."
        )
        self.statusregel.setText(
            f"Workflow {summary.get('status', 'voltooid')}."
        )
        self._last_summary = summary
        setnaam = summary.get("recovery_set") or "Onbekend"
        self.recovery_set_label.setText(setnaam)
        self.playlist_id = summary.get("playlist_id")
        self.playlist_knop.setEnabled(bool(self.playlist_id))
        self.eindoverzicht.setText(
            f"Recovery-set: {setnaam}\n"
            f"Eindstatus: {summary.get('status', 'onbekend')}\n"
            f"MP3-bestanden: {summary.get('total_mp3', 0)} | "
            f"OK: {summary.get('ok', 0)} | "
            f"FFmpeg-fouten: {summary.get('ffmpeg_errors', 0)} | "
            f"0-byte: {summary.get('zero_byte', 0)} | "
            f"Recovery-items: {summary.get('recovery_items', 0)}\n"
            f"MATCHED: {summary.get('matched', 0)} | "
            f"LOW_CONFIDENCE: {summary.get('low_confidence', 0)} | "
            f"MANUAL_REVIEW: {summary.get('manual_review', 0)} | "
            f"NOT_FOUND: {summary.get('not_found', 0)} | "
            f"Technische Spotify-fouten: "
            f"{summary.get('spotify_errors', 0)}\n"
            f"Nieuw aan playlist: {summary.get('playlist_added', 0)} | "
            f"Reeds aanwezig: {summary.get('playlist_existing', 0)} | "
            f"Playlist: {summary.get('playlist_name') or 'niet beschikbaar'}"
            f"\nRapport: {summary.get('report_path') or 'niet beschikbaar'}"
        )
    def _open_recovery_review(self, summary):
        self.review_active = True
        self.start_knop.setEnabled(False)
        self.bladeren_knop.setEnabled(False)
        self._set_stage_state("Recovery Review", "actief")
        self.huidige_stap_label.setText("Recovery Review")
        self.huidige_activiteit_label.setText(
            "Recovery-items handmatig beoordelen"
        )
        self.statusregel.setText("Recovery Review wordt uitgevoerd…")
        self._append_log(
            "Recovery Review geopend; er is nog geen playlist aangemaakt."
        )
        try:
            dialog = self.review_factory(
                summary["recovery_set_id"],
                summary["recovery_set"],
                self,
            )
        except Exception as error:
            self.review_active = False
            self._set_stage_state(
                "Recovery Review", "mislukt", str(error)
            )
            self._append_log(f"Recovery Review kon niet openen: {error}")
            QMessageBox.critical(
                self, "Recovery Review", str(error)
            )
            return
        self.review_dialog = dialog
        dialog.finished.connect(self._review_finished)
        dialog.show()

    def _review_finished(self, result):
        self.review_active = False
        accepted = result == QDialog.DialogCode.Accepted
        try:
            if accepted:
                self.statusregel.setText(
                    "Recovery Review opgeslagen; workflow wordt hervat."
                )
                self._append_log(
                    "Recovery Review opgeslagen. "
                    "Er is geen playlist aangemaakt."
                )
            else:
                self.statusregel.setText(
                    "Recovery Review gesloten; workflow wordt hervat."
                )
                self._append_log(
                    "Recovery Review gesloten; "
                    "er is geen playlist aangemaakt."
                )
            self.huidige_stap_label.setText("Recovery Review")
            self.huidige_activiteit_label.setText(
                "Backendworkflow hervatten"
            )
            self.review_dialog = None
        finally:
            if self.workflow_worker is not None:
                self.workflow_worker.resolve_review(accepted)

    def _workflow_failed(self, error):
        for stage, label in self.stage_labels.items():
            toestand = label.property("workflowState")
            if toestand == "actief":
                self._set_stage_state(stage, "mislukt", error)
            elif toestand == "wachtend":
                self._set_stage_state(
                    stage, "overgeslagen",
                    "Workflow eerder mislukt.",
                )
        self.huidige_stap_label.setText("Mislukt")
        self.huidige_activiteit_label.setText(error)
        self.statusregel.setText("Workflow mislukt.")
        self.eindoverzicht.setText(
            f"Recovery-set: {self.recovery_set_label.text()}\n"
            f"Eindstatus: mislukt\nFout: {error}"
        )
        QMessageBox.critical(self, "Workflow mislukt", error)

    def _thread_finished(self):
        self.thread_finished_count += 1
        self._thread_done = True
        self._maybe_finalize_thread()

    def _worker_finished(self):
        self._worker_done = True
        self._maybe_finalize_thread()

    def _maybe_finalize_thread(self):
        if not (self._worker_done and self._thread_done):
            return
        self.workflow_running = False
        self.start_knop.setEnabled(not self.review_active)
        self.bladeren_knop.setEnabled(not self.review_active)
        if self.statusregel.text() == "Workflow wordt uitgevoerd…":
            self.statusregel.setText("Workflow beëindigd.")

    def _open_playlist(self):
        url = spotify_playlist_url(self.playlist_id)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def closeEvent(self, event: QCloseEvent):
        if self._thread_actief():
            QMessageBox.information(
                self,
                "Workflow actief",
                "De workflow wordt nog uitgevoerd. "
                "Wacht tot deze klaar is.",
            )
            event.ignore()
            return
        event.accept()
