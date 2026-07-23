"""Hoofdvenster dat bestaande Megaman-kernfuncties orkestreert."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
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

from analyse import voer_analyse
from database import DATABASE_BESTAND, SQLiteDatabase
from rar_extractor import voer_extractie_uit

from gui.workers import ActionWorker


STATISTIEKEN = (
    ("mp3", "Gevonden MP3-bestanden"),
    ("rar", "Gevonden RAR-sets"),
    ("par2", "Gevonden PAR2-datasets"),
    ("complete", "COMPLETE"),
    ("repairable", "REPAIRABLE"),
    ("not_repairable", "NOT_REPAIRABLE"),
)


def _voer_reparatie_uit(map_pad, uitvoer=None):
    try:
        from par2_repair import voer_par2_reparatie_uit
    except ImportError as fout:
        raise RuntimeError(
            "PAR2-reparatie is in deze versie niet beschikbaar."
        ) from fout
    return voer_par2_reparatie_uit(map_pad, uitvoer=uitvoer)


def _toon_rapport(uitvoer=None):
    from cli import toon_laatste_rapport
    code = toon_laatste_rapport(uitvoer=uitvoer)
    if code:
        raise RuntimeError("Er is geen rapport beschikbaar.")
    return code


def lees_statistieken(database_pad=DATABASE_BESTAND):
    leeg = {sleutel: 0 for sleutel, _ in STATISTIEKEN}
    database_pad = Path(database_pad)
    if not database_pad.is_file():
        return leeg
    database = SQLiteDatabase(database_pad)
    try:
        mp3 = database.verbinding.execute(
            "SELECT COUNT(*) AS aantal FROM mp3_bestanden WHERE bestaat = 1"
        ).fetchone()["aantal"]
        rar = database.verbinding.execute(
            "SELECT COUNT(*) AS aantal FROM rar_sets WHERE actief = 1"
        ).fetchone()["aantal"]
        par = database.verbinding.execute(
            """
            SELECT
              SUM(CASE WHEN aantal_par_bestanden > 0 THEN 1 ELSE 0 END) par2,
              SUM(CASE WHEN status = 'COMPLETE' THEN 1 ELSE 0 END) complete,
              SUM(CASE WHEN status = 'REPAIRABLE' THEN 1 ELSE 0 END)
                repairable,
              SUM(CASE WHEN status = 'NOT_REPAIRABLE' THEN 1 ELSE 0 END)
                not_repairable
            FROM par_inventory
            """
        ).fetchone()
        return {
            "mp3": mp3 or 0,
            "rar": rar or 0,
            "par2": par["par2"] or 0,
            "complete": par["complete"] or 0,
            "repairable": par["repairable"] or 0,
            "not_repairable": par["not_repairable"] or 0,
        }
    finally:
        database.sluit()


class MegamanMainWindow(QMainWindow):
    def __init__(
        self,
        worker_factory=ActionWorker,
        statistics_reader=lees_statistieken,
    ):
        super().__init__()
        self.worker_factory = worker_factory
        self.statistics_reader = statistics_reader
        self.worker = None
        self.setWindowTitle("Megaman Recovery Tool")
        self.resize(900, 650)
        self._bouw_interface()
        self.vernieuw_statistieken()

    def _bouw_interface(self):
        centraal = QWidget()
        layout = QVBoxLayout(centraal)

        maprij = QHBoxLayout()
        self.map_invoer = QLineEdit()
        self.map_invoer.setPlaceholderText("Selecteer een downloadmap")
        self.bladeren_knop = QPushButton("Bladeren")
        self.bladeren_knop.clicked.connect(self._bladeren)
        maprij.addWidget(self.map_invoer)
        maprij.addWidget(self.bladeren_knop)
        layout.addLayout(maprij)

        actierij = QHBoxLayout()
        self.analyseren_knop = QPushButton("Analyseren")
        self.repareren_knop = QPushButton("Repareren")
        self.uitpakken_knop = QPushButton("Uitpakken")
        self.rapport_knop = QPushButton("Rapport tonen")
        self.actieknoppen = (
            self.analyseren_knop, self.repareren_knop,
            self.uitpakken_knop, self.rapport_knop,
        )
        for knop in self.actieknoppen:
            actierij.addWidget(knop)
        layout.addLayout(actierij)

        self.analyseren_knop.clicked.connect(self._analyseer)
        self.repareren_knop.clicked.connect(self._repareer)
        self.uitpakken_knop.clicked.connect(self._pak_uit)
        self.rapport_knop.clicked.connect(self._rapport)

        statistieken = QGridLayout()
        self.statistiek_labels = {}
        for index, (sleutel, titel) in enumerate(STATISTIEKEN):
            statistieken.addWidget(QLabel(titel + ":"), index // 3, 2 * (index % 3))
            waarde = QLabel("0")
            waarde.setAlignment(Qt.AlignmentFlag.AlignRight)
            statistieken.addWidget(waarde, index // 3, 2 * (index % 3) + 1)
            self.statistiek_labels[sleutel] = waarde
        layout.addLayout(statistieken)

        self.voortgang = QProgressBar()
        self.voortgang.setRange(0, 100)
        layout.addWidget(self.voortgang)
        self.statusregel = QLabel("Gereed")
        layout.addWidget(self.statusregel)
        self.logvenster = QPlainTextEdit()
        self.logvenster.setReadOnly(True)
        layout.addWidget(self.logvenster, stretch=1)
        self.setCentralWidget(centraal)

    def _bladeren(self):
        map_pad = QFileDialog.getExistingDirectory(
            self, "Selecteer een map", self.map_invoer.text()
        )
        if map_pad:
            self.map_invoer.setText(map_pad)

    def _geldige_map(self):
        map_pad = Path(self.map_invoer.text().strip().strip('"'))
        if not self.map_invoer.text().strip() or not map_pad.is_dir():
            QMessageBox.warning(
                self, "Ongeldige map",
                "Selecteer eerst een bestaande map."
            )
            return None
        return map_pad

    def _analyseer(self):
        map_pad = self._geldige_map()
        if map_pad:
            self._start_actie(
                "Analyseren", voer_analyse, map_pad, map_pad
            )

    def _repareer(self):
        map_pad = self._geldige_map()
        if map_pad is None:
            return
        antwoord = QMessageBox.question(
            self, "Reparatie bevestigen",
            "Reparatie kan bestanden wijzigen of aanmaken. Doorgaan?",
        )
        if antwoord == QMessageBox.StandardButton.Yes:
            self._start_actie("Repareren", _voer_reparatie_uit, map_pad)

    def _pak_uit(self):
        map_pad = self._geldige_map()
        if map_pad is None:
            return
        antwoord = QMessageBox.question(
            self, "Uitpakken bevestigen",
            "Uitpakken maakt bestanden aan. Doorgaan?",
        )
        if antwoord == QMessageBox.StandardButton.Yes:
            self._start_actie("Uitpakken", voer_extractie_uit, map_pad)

    def _rapport(self):
        self._start_actie("Rapport tonen", _toon_rapport)

    def _start_actie(self, naam, actie, *args):
        self._zet_actief(False)
        self.voortgang.setValue(0)
        self.statusregel.setText(f"{naam}...")
        self.logvenster.appendPlainText(f"{naam} gestart.")
        self.worker = self.worker_factory(actie, *args)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self.voortgang.setValue)
        self.worker.succeeded.connect(
            lambda resultaat: self.statusregel.setText(
                f"{naam} voltooid."
            )
        )
        self.worker.failed.connect(self._actie_mislukt)
        self.worker.completed.connect(self._actie_afgerond)
        self.worker.start()

    def _log(self, tekst):
        self.logvenster.moveCursor(
            QTextCursor.MoveOperation.End
        )
        self.logvenster.insertPlainText(tekst)
        self.logvenster.ensureCursorVisible()

    def _actie_mislukt(self, melding):
        melding = melding or "Onbekende fout."
        self.statusregel.setText("Actie mislukt.")
        self.logvenster.appendPlainText(f"FOUT: {melding}")
        QMessageBox.critical(self, "Actie mislukt", melding)

    def _actie_afgerond(self):
        self._zet_actief(True)
        self.vernieuw_statistieken()

    def _zet_actief(self, actief):
        self.bladeren_knop.setEnabled(actief)
        self.map_invoer.setEnabled(actief)
        for knop in self.actieknoppen:
            knop.setEnabled(actief)

    def vernieuw_statistieken(self):
        try:
            waarden = self.statistics_reader()
        except Exception as fout:
            self.logvenster.appendPlainText(
                f"Statistieken niet beschikbaar: {fout}"
            )
            waarden = {}
        for sleutel, label in self.statistiek_labels.items():
            label.setText(str(waarden.get(sleutel, 0)))
