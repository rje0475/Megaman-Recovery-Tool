"""Recovery Review Wizard between Spotify Search and playlist creation."""

from functools import partial

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.recovery_review import (
    bewaar_recovery_review,
    laad_recovery_review,
)
from database import DATABASE_BESTAND, SQLiteDatabase


KOLOMMEN = (
    "",
    "Jaar",
    "Week",
    "Positie",
    "Originele bestandsnaam",
    "Artiest",
    "Titel",
    "Status",
    "Spotify-status",
)


class RecoveryReviewDialog(QDialog):
    def __init__(
        self, recovery_set_id, recovery_set_name, parent=None,
        database_factory=SQLiteDatabase, database_path=DATABASE_BESTAND,
    ):
        super().__init__(parent)
        self.recovery_set_id = int(recovery_set_id)
        self.recovery_set_name = recovery_set_name
        self.database_factory = database_factory
        self.database_path = database_path
        self.database = database_factory(database_path)
        self.items = laad_recovery_review(
            self.database, self.recovery_set_id
        )
        self._candidate_groups = {}
        self._retired_candidate_widgets = []
        self._candidate_choice = {
            item.id: next(
                (
                    kandidaat.id
                    for kandidaat in item.candidates
                    if kandidaat.selected
                ),
                None,
            )
            for item in self.items
        }
        self._network = QNetworkAccessManager(self)
        self.setWindowTitle(f"Recovery Review — {recovery_set_name}")
        self.resize(1280, 760)
        self.setModal(True)
        self._bouw_interface()
        self._vul_tabel()
        self._update_summary()

    def _bouw_interface(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Recovery Review: {self.recovery_set_name}\n"
            "Beoordeel de vervangingen. Er wordt nog geen playlist gemaakt."
        ))

        selectie = QHBoxLayout()
        for tekst, actie in (
            ("Select All", self._select_all),
            ("Select None", self._select_none),
            ("Invert Selection", self._invert_selection),
        ):
            knop = QPushButton(tekst)
            knop.clicked.connect(actie)
            selectie.addWidget(knop)
        selectie.addStretch(1)
        layout.addLayout(selectie)

        splitter = QSplitter()
        self.table = QTableWidget(0, len(KOLOMMEN))
        self.table.setHorizontalHeaderLabels(KOLOMMEN)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.table.itemSelectionChanged.connect(self._toon_selectie)
        self.table.itemChanged.connect(self._item_changed)
        self.table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self.table)

        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        self.detail_widget = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_widget)
        self.detail_labels = {}
        detail_grid = QGridLayout()
        for rij, (key, label) in enumerate((
            ("week", "Week"),
            ("position", "Chartpositie"),
            ("filename", "Originele bestandsnaam"),
            ("recovery", "Recovery status"),
            ("spotify", "Spotify status"),
            ("matches", "Aantal Spotify matches"),
        )):
            detail_grid.addWidget(QLabel(f"{label}:"), rij, 0)
            waarde = QLabel("—")
            waarde.setWordWrap(True)
            detail_grid.addWidget(waarde, rij, 1)
            self.detail_labels[key] = waarde
        self.detail_layout.addLayout(detail_grid)
        self.candidates_box = QGroupBox("Spotify-resultaten")
        self.candidates_layout = QVBoxLayout(self.candidates_box)
        self.detail_layout.addWidget(self.candidates_box)
        self.youtube_button = QPushButton("Search YouTube")
        self.youtube_button.setEnabled(False)
        self.detail_layout.addWidget(self.youtube_button)
        self.detail_layout.addStretch(1)
        detail_scroll.setWidget(self.detail_widget)
        splitter.addWidget(detail_scroll)
        splitter.setSizes([820, 440])
        layout.addWidget(splitter, stretch=1)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        onder = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.continue_button = QPushButton("Continue")
        self.cancel_button = QPushButton("Cancel")
        self.continue_button.setEnabled(False)
        self.back_button.clicked.connect(self._back)
        self.continue_button.clicked.connect(self._continue)
        self.cancel_button.clicked.connect(self.reject)
        onder.addWidget(self.back_button)
        onder.addStretch(1)
        onder.addWidget(self.continue_button)
        onder.addWidget(self.cancel_button)
        layout.addLayout(onder)

    def _vul_tabel(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            checkbox.setCheckState(
                Qt.CheckState.Checked
                if item.selected_for_playlist
                else Qt.CheckState.Unchecked
            )
            checkbox.setData(Qt.ItemDataRole.UserRole, item.id)
            self.table.setItem(row, 0, checkbox)
            waarden = (
                item.year,
                item.week,
                item.chart_position,
                item.original_filename,
                item.artist,
                item.title,
                item.recovery_status,
                item.spotify_status,
            )
            for column, waarde in enumerate(waarden, 1):
                tabelitem = QTableWidgetItem(
                    "—" if waarde in (None, "") else str(waarde)
                )
                tabelitem.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                self.table.setItem(row, column, tabelitem)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        if self.items:
            self.table.selectRow(0)

    def _selected_ids(self):
        return {
            self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState()
            == Qt.CheckState.Checked
        }

    def _set_all(self, state):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)
        self.table.blockSignals(False)
        self._update_summary()

    def _select_all(self):
        self._set_all(Qt.CheckState.Checked)

    def _select_none(self):
        self._set_all(Qt.CheckState.Unchecked)

    def _invert_selection(self):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
        self.table.blockSignals(False)
        self._update_summary()

    def _item_changed(self, _item):
        self._update_summary()

    def _update_summary(self):
        telling = {
            "Match": 0,
            "Multiple Matches": 0,
            "Manual Review": 0,
            "Low Confidence": 0,
            "Not Found": 0,
        }
        for item in self.items:
            telling[item.spotify_status] = (
                telling.get(item.spotify_status, 0) + 1
            )
        geselecteerd = len(self._selected_ids())
        self.summary_label.setText(
            f"Recovery items: {len(self.items)} | "
            f"Matched: {telling['Match']} | "
            f"Multiple Matches: {telling['Multiple Matches']} | "
            f"Manual Review: {telling['Manual Review']} | "
            f"Low Confidence: {telling['Low Confidence']} | "
            f"Not Found: {telling['Not Found']} | "
            f"Selected for playlist: {geselecteerd}"
        )
        self.continue_button.setEnabled(geselecteerd > 0)

    def _clear_candidates(self):
        while self.candidates_layout.count():
            child = self.candidates_layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.hide()
                widget.setParent(self)
                self._retired_candidate_widgets.append(widget)

    def _toon_selectie(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        self.detail_labels["week"].setText(
            "—" if item.week is None else str(item.week)
        )
        self.detail_labels["position"].setText(
            "—" if item.chart_position is None
            else str(item.chart_position)
        )
        self.detail_labels["filename"].setText(item.original_filename)
        self.detail_labels["recovery"].setText(item.recovery_status)
        self.detail_labels["spotify"].setText(item.spotify_status)
        self.detail_labels["matches"].setText(str(len(item.candidates)))
        self._clear_candidates()
        groep = QButtonGroup(self)
        groep.setExclusive(True)
        self._candidate_groups[item.id] = groep
        if not item.candidates:
            self.candidates_layout.addWidget(QLabel(
                "No Spotify match found."
            ))
            self.youtube_button.setVisible(True)
            return
        self.youtube_button.setVisible(False)
        for kandidaat in item.candidates:
            kaart = QGroupBox()
            rij = QHBoxLayout(kaart)
            cover = QLabel("Geen\ncover")
            cover.setFixedSize(72, 72)
            cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rij.addWidget(cover)
            radio = QRadioButton()
            radio.setProperty("candidate_id", kandidaat.id)
            radio.setChecked(
                self._candidate_choice.get(item.id) == kandidaat.id
            )
            radio.toggled.connect(partial(
                self._candidate_toggled, item.id, kandidaat.id
            ))
            groep.addButton(radio)
            rij.addWidget(radio)
            duur = (
                f"{kandidaat.duration_ms // 60000}:"
                f"{(kandidaat.duration_ms // 1000) % 60:02d}"
                if kandidaat.duration_ms else "—"
            )
            tekst = QLabel(
                f"{kandidaat.artist} — {kandidaat.title}\n"
                f"Album: {kandidaat.album or '—'} | Duur: {duur} | "
                f"Popularity: "
                f"{kandidaat.popularity if kandidaat.popularity is not None else '—'}\n"
                f"Confidence: {kandidaat.confidence:.0%}"
            )
            tekst.setWordWrap(True)
            rij.addWidget(tekst, stretch=1)
            self.candidates_layout.addWidget(kaart)
            if kandidaat.album_cover_url:
                reply = self._network.get(QNetworkRequest(
                    QUrl(kandidaat.album_cover_url)
                ))
                reply.finished.connect(partial(
                    self._cover_loaded, reply, cover
                ))

    def _cover_loaded(self, reply, label):
        data = reply.readAll()
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            label.setPixmap(pixmap.scaled(
                label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        reply.deleteLater()

    def _candidate_toggled(self, item_id, kandidaat_id, checked):
        if checked:
            self._candidate_choice[item_id] = kandidaat_id

    def _gekozen_kandidaten(self):
        return {
            item_id: kandidaat_id
            for item_id, kandidaat_id in self._candidate_choice.items()
            if kandidaat_id is not None
        }

    def _continue(self):
        try:
            bewaar_recovery_review(
                self.database,
                self.recovery_set_id,
                self._selected_ids(),
                self._gekozen_kandidaten(),
            )
        except Exception as error:
            QMessageBox.critical(
                self, "Recovery Review", f"Review opslaan mislukt: {error}"
            )
            return
        self.accept()

    def _back(self):
        self.done(2)

    def closeEvent(self, event):
        self.database.sluit()
        super().closeEvent(event)

    def done(self, result):
        self.database.sluit()
        super().done(result)
