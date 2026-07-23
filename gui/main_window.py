"""Hoofdvenster dat bestaande Megaman-kernfuncties orkestreert."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from analyse import voer_analyse
from database import DATABASE_BESTAND, SQLiteDatabase
from rar_extractor import voer_extractie_uit

from gui.workers import ActionWorker
from spotify_smart import (
    kies_kandidaat,
    markeer_geen_kandidaat,
    voer_spotify_smart_uit,
)


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
        self._bouw_spotify(layout)

        self.voortgang = QProgressBar()
        self.voortgang.setRange(0, 100)
        layout.addWidget(self.voortgang)
        self.statusregel = QLabel("Gereed")
        layout.addWidget(self.statusregel)
        self.logvenster = QPlainTextEdit()
        self.logvenster.setReadOnly(True)
        layout.addWidget(self.logvenster, stretch=1)
        self.setCentralWidget(centraal)

    def _bouw_spotify(self, layout):
        layout.addWidget(QLabel("Spotify-kandidaten"))
        rij = QHBoxLayout()
        self.spotify_filter = QComboBox()
        self.spotify_filter.addItems([
            "Alles", "FOUND", "AMBIGUOUS", "NOT_FOUND",
            "INSUFFICIENT_IDENTITY", "MANUAL", "REVIEWED_NONE",
        ])
        self.spotify_zoeken_knop = QPushButton("Spotify zoeken")
        self.spotify_retry_knop = QPushButton(
            "Mislukte resultaten opnieuw proberen"
        )
        self.kandidaten_knop = QPushButton("Kandidaten bekijken")
        self.spotify_openen_knop = QPushButton(
            "Gekozen resultaat openen in Spotify"
        )
        self.geen_kandidaat_knop = QPushButton(
            "Markeren als geen juiste kandidaat"
        )
        for widget in (
            self.spotify_filter, self.spotify_zoeken_knop,
            self.spotify_retry_knop, self.kandidaten_knop,
            self.spotify_openen_knop, self.geen_kandidaat_knop,
        ):
            rij.addWidget(widget)
        layout.addLayout(rij)
        self.spotify_tabel = QTableWidget(0, 9)
        self.spotify_tabel.setHorizontalHeaderLabels([
            "Lokaal bestand", "Oorspronkelijke artiest",
            "Oorspronkelijke titel", "Lokale versie",
            "Spotify-artiest", "Spotify-titel", "Spotify-versie",
            "Score", "Status",
        ])
        layout.addWidget(self.spotify_tabel)
        self.spotify_statistieken = QLabel(
            "Te beoordelen: 0 | FOUND: 0 | AMBIGUOUS: 0 | NOT_FOUND: 0 | "
            "INSUFFICIENT_IDENTITY: 0 | MANUAL: 0 | REVIEWED_NONE: 0"
        )
        layout.addWidget(self.spotify_statistieken)
        self.actieknoppen += (
            self.spotify_zoeken_knop, self.spotify_retry_knop,
            self.kandidaten_knop, self.spotify_openen_knop,
            self.geen_kandidaat_knop,
        )
        self.spotify_filter.currentTextChanged.connect(
            lambda _: self.vernieuw_spotify()
        )
        self.spotify_zoeken_knop.clicked.connect(
            lambda: self._spotify_zoek(False)
        )
        self.spotify_retry_knop.clicked.connect(
            lambda: self._spotify_zoek(True)
        )
        self.kandidaten_knop.clicked.connect(self._toon_kandidaten)
        self.spotify_openen_knop.clicked.connect(self._open_spotify)
        self.geen_kandidaat_knop.clicked.connect(self._geen_kandidaat)

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
        self.vernieuw_spotify()

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
        self.vernieuw_spotify()

    def _spotify_zoek(self, retry):
        map_pad = self._geldige_map()
        if map_pad:
            def actie(geselecteerde_map, uitvoer=None):
                return voer_spotify_smart_uit(
                    geselecteerde_map, retry=retry, uitvoer=uitvoer
                )
            self._start_actie(
                "Spotify opnieuw proberen" if retry else "Spotify zoeken",
                actie, map_pad,
            )

    def vernieuw_spotify(self):
        self.spotify_tabel.setRowCount(0)
        if not Path(DATABASE_BESTAND).is_file():
            return
        database = SQLiteDatabase(DATABASE_BESTAND)
        try:
            filter_ = self.spotify_filter.currentText()
            rijen = database.verbinding.execute(
                """
                SELECT * FROM spotify_smart_results
                WHERE ?='Alles' OR status=?
                ORDER BY recovery_item_id
                """, (filter_, filter_)
            ).fetchall()
            for rij in rijen:
                index = self.spotify_tabel.rowCount()
                self.spotify_tabel.insertRow(index)
                waarden = (
                    rij["local_path"], rij["original_artist"],
                    rij["original_title"], rij["local_version"],
                    rij["found_artist"], rij["found_title"],
                    rij["found_version"], rij["match_score"], rij["status"],
                )
                for kolom, waarde in enumerate(waarden):
                    item = QTableWidgetItem(
                        "" if waarde is None else str(waarde)
                    )
                    item.setData(
                        Qt.ItemDataRole.UserRole, rij["recovery_item_id"]
                    )
                    self.spotify_tabel.setItem(index, kolom, item)
            telling = {
                rij["status"]: rij["aantal"]
                for rij in database.verbinding.execute(
                    """
                    SELECT status, COUNT(*) aantal
                    FROM spotify_smart_results GROUP BY status
                    """
                )
            }
            totaal = sum(telling.values())
            self.spotify_statistieken.setText(
                f"Te beoordelen: {totaal} | FOUND: {telling.get('FOUND', 0)} "
                f"| AMBIGUOUS: {telling.get('AMBIGUOUS', 0)} | NOT_FOUND: "
                f"{telling.get('NOT_FOUND', 0)} | INSUFFICIENT_IDENTITY: "
                f"{telling.get('INSUFFICIENT_IDENTITY', 0)} | MANUAL: "
                f"{telling.get('MANUAL', 0)} | REVIEWED_NONE: "
                f"{telling.get('REVIEWED_NONE', 0)}"
            )
        finally:
            database.sluit()

    def _geselecteerd_item_id(self):
        rij = self.spotify_tabel.currentRow()
        if rij < 0 or self.spotify_tabel.item(rij, 0) is None:
            QMessageBox.warning(self, "Geen selectie", "Selecteer eerst een item.")
            return None
        return self.spotify_tabel.item(rij, 0).data(Qt.ItemDataRole.UserRole)

    def _toon_kandidaten(self):
        item_id = self._geselecteerd_item_id()
        if item_id is not None:
            SpotifyKandidatenDialoog(item_id, self).exec()
            self.vernieuw_spotify()

    def _geen_kandidaat(self):
        item_id = self._geselecteerd_item_id()
        if item_id is None:
            return
        database = SQLiteDatabase(DATABASE_BESTAND)
        try:
            markeer_geen_kandidaat(database, item_id)
        finally:
            database.sluit()
        self.vernieuw_spotify()

    def _open_spotify(self):
        item_id = self._geselecteerd_item_id()
        if item_id is None:
            return
        database = SQLiteDatabase(DATABASE_BESTAND)
        try:
            rij = database.verbinding.execute(
                """
                SELECT spotify_url FROM spotify_smart_results
                WHERE recovery_item_id=?
                """, (item_id,)
            ).fetchone()
        finally:
            database.sluit()
        url = rij["spotify_url"] if rij else None
        if not url or not url.startswith("https://open.spotify.com/track/"):
            QMessageBox.warning(self, "Ongeldige URL", "Geen geldige Spotify-URL.")
            return
        QDesktopServices.openUrl(QUrl(url))


class SpotifyKandidatenDialoog(QDialog):
    def __init__(self, recovery_item_id, parent=None):
        super().__init__(parent)
        self.recovery_item_id = recovery_item_id
        self.setWindowTitle("Spotify-kandidaten")
        layout = QVBoxLayout(self)
        self.tabel = QTableWidget(0, 10)
        self.tabel.setHorizontalHeaderLabels([
            "Rang", "Artiest", "Titel", "Album", "Duur", "Versie",
            "Remixer", "Score", "Reden", "Spotify-link",
        ])
        layout.addWidget(self.tabel)
        knoppen = QHBoxLayout()
        for tekst, methode in (
            ("Deze versie kiezen", self._kies),
            ("Openen in Spotify", self._open),
            ("Geen van deze", self._geen),
            ("Later beoordelen", self.reject),
        ):
            knop = QPushButton(tekst)
            knop.clicked.connect(methode)
            knoppen.addWidget(knop)
        layout.addLayout(knoppen)
        self._laad()

    def _laad(self):
        database = SQLiteDatabase(DATABASE_BESTAND)
        try:
            rijen = database.verbinding.execute(
                """
                SELECT * FROM spotify_candidates
                WHERE recovery_item_id=? ORDER BY rank_number
                """, (self.recovery_item_id,)
            ).fetchall()
        finally:
            database.sluit()
        for rij in rijen:
            index = self.tabel.rowCount()
            self.tabel.insertRow(index)
            waarden = (
                rij["rank_number"], rij["artist"], rij["title"], rij["album"],
                rij["duration_ms"], rij["version"], rij["remixer"],
                rij["total_score"], rij["score_reason"], rij["spotify_url"],
            )
            for kolom, waarde in enumerate(waarden):
                item = QTableWidgetItem("" if waarde is None else str(waarde))
                item.setData(Qt.ItemDataRole.UserRole, rij["id"])
                self.tabel.setItem(index, kolom, item)

    def _huidige_id(self):
        rij = self.tabel.currentRow()
        return (
            self.tabel.item(rij, 0).data(Qt.ItemDataRole.UserRole)
            if rij >= 0 and self.tabel.item(rij, 0) else None
        )

    def _kies(self):
        kandidaat_id = self._huidige_id()
        if kandidaat_id is None:
            return
        database = SQLiteDatabase(DATABASE_BESTAND)
        try:
            kies_kandidaat(database, self.recovery_item_id, kandidaat_id)
        finally:
            database.sluit()
        self.accept()

    def _geen(self):
        database = SQLiteDatabase(DATABASE_BESTAND)
        try:
            markeer_geen_kandidaat(database, self.recovery_item_id)
        finally:
            database.sluit()
        self.accept()

    def _open(self):
        rij = self.tabel.currentRow()
        url = self.tabel.item(rij, 9).text() if rij >= 0 else ""
        if url.startswith("https://open.spotify.com/track/"):
            QDesktopServices.openUrl(QUrl(url))
        else:
            QMessageBox.warning(self, "Ongeldige URL", "Geen geldige Spotify-URL.")
