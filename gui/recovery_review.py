"""Recovery Review Wizard tussen Spotify Search en playlistcreatie."""

import json

from functools import partial

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.recovery_review import (
    bewaar_recovery_review,
    laad_recovery_review,
    selecteer_spotify_kandidaat,
    stel_playlist_selectie_in,
)
from database import DATABASE_BESTAND, SQLiteDatabase
from core.youtube.provider import YouTubeSearchProvider
from core.youtube.search import (
    laad_youtube_kandidaten,
    markeer_geen_youtube_bron,
    selecteer_youtube_kandidaat,
)
from gui.workers import YouTubeSearchWorker


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
    "Confidence",
)
FILTERS = (
    "Alles",
    "Klaar voor playlist",
    "Nog te beoordelen",
    "Geen match",
    "Low confidence",
    "FFmpeg-fout",
    "0-byte",
    "Missing",
    "YouTube nog niet gezocht",
    "YouTube kandidaten gevonden",
    "YouTube-bron gekozen",
    "Geen geschikte YouTube-bron",
    "Klaar voor toekomstige recovery",
)


class PlaylistConfirmationDialog(QDialog):
    def __init__(self, counts, default_name, parent=None):
        super().__init__(parent)
        self.action = "back"
        self.setWindowTitle("Playlist voorbereiden")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Totaal recovery-items: {counts['total']}\n"
            f"Aangevinkte items: {counts['selected']}\n"
            f"Items met gekozen Spotify-match: {counts['with_match']}\n"
            f"Items klaar voor playlist: {counts['ready']}\n"
            f"Items zonder gekozen match: {counts['without_match']}\n"
            f"Gedeselecteerde items: {counts['deselected']}"
        ))
        layout.addWidget(QLabel("Playlistnaam:"))
        self.name_field = QLineEdit(default_name)
        layout.addWidget(self.name_field)
        buttons = QHBoxLayout()
        terug = QPushButton("Terug naar review")
        maken = QPushButton("Playlist maken")
        annuleren = QPushButton("Annuleren")
        terug.clicked.connect(lambda: self._finish("back"))
        maken.clicked.connect(lambda: self._finish("create"))
        annuleren.clicked.connect(lambda: self._finish("cancel"))
        buttons.addWidget(terug)
        buttons.addStretch(1)
        buttons.addWidget(maken)
        buttons.addWidget(annuleren)
        layout.addLayout(buttons)

    @property
    def playlist_name(self):
        return self.name_field.text().strip()

    def _finish(self, action):
        if action == "create" and not self.playlist_name:
            QMessageBox.warning(self, "Playlist", "Geef een playlistnaam op.")
            return
        self.action = action
        self.accept() if action == "create" else self.reject()


class PlaylistResultDialog(QDialog):
    def __init__(self, summary, report_path=None, parent=None):
        super().__init__(parent)
        self.summary = summary
        self.report_path = report_path
        self.setWindowTitle("Spotify-playlistresultaat")
        layout = QVBoxLayout(self)
        self.result_label = QLabel(
            f"Playlistnaam: {summary.get('playlist_name') or '—'}\n"
            f"Unieke geselecteerde tracks: {summary.get('unique_selected', 0)}\n"
            f"Reeds aanwezig: {summary.get('playlist_existing', 0)}\n"
            f"Nieuw toegevoegd: {summary.get('playlist_added', 0)}\n"
            f"Duplicaten overgeslagen: {summary.get('duplicates_skipped', 0)}\n"
            f"Items zonder match: {summary.get('unmatched_selected', 0)}\n"
            f"Playlist-URL: {summary.get('playlist_url') or '—'}\n"
            f"Syncstatus: {summary.get('playlist_sync_status') or '—'}"
        )
        layout.addWidget(self.result_label)
        buttons = QHBoxLayout()
        self.open_playlist_button = QPushButton("Playlist openen")
        self.open_playlist_button.setEnabled(bool(summary.get("playlist_url")))
        self.open_playlist_button.clicked.connect(self._open_playlist)
        self.open_report_button = QPushButton("Rapport openen")
        self.open_report_button.setEnabled(bool(report_path))
        self.open_report_button.clicked.connect(self._open_report)
        terug = QPushButton("Terug naar hoofdscherm")
        terug.clicked.connect(self.accept)
        buttons.addWidget(self.open_playlist_button)
        buttons.addWidget(self.open_report_button)
        buttons.addStretch(1)
        buttons.addWidget(terug)
        layout.addLayout(buttons)

    def _open_playlist(self):
        QDesktopServices.openUrl(QUrl(self.summary["playlist_url"]))

    def _open_report(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report_path)))


class SorteerItem(QTableWidgetItem):
    def __init__(self, waarde=None, tekst=None):
        super().__init__(
            "—" if waarde in (None, "") else str(waarde)
            if tekst is None else tekst
        )
        self.setData(Qt.ItemDataRole.UserRole + 1, waarde)

    def __lt__(self, other):
        links = self.data(Qt.ItemDataRole.UserRole + 1)
        rechts = other.data(Qt.ItemDataRole.UserRole + 1)
        if links is None:
            return False
        if rechts is None:
            return True
        if isinstance(links, str) or isinstance(rechts, str):
            return str(links).casefold() < str(rechts).casefold()
        return links < rechts


class RecoveryReviewDialog(QDialog):
    def __init__(
        self, recovery_set_id, recovery_set_name, parent=None,
        database_factory=SQLiteDatabase, database_path=DATABASE_BESTAND,
    ):
        super().__init__(parent)
        self.recovery_set_id = int(recovery_set_id)
        self.recovery_set_name = recovery_set_name
        self.database = database_factory(database_path)
        self.items = laad_recovery_review(
            self.database, self.recovery_set_id
        )
        self._items_by_id = {item.id: item for item in self.items}
        self._candidate_choice = {
            item.id: item.selected_candidate_id for item in self.items
        }
        self._candidate_groups = {}
        self._retired_candidate_widgets = []
        self._network = QNetworkAccessManager(self)
        self.youtube_thread = None
        self.youtube_worker = None
        self._building_table = False
        self.playlist_requested = False
        self.prepared_playlist_name = recovery_set_name
        self.setWindowTitle(f"Recovery Review — {recovery_set_name}")
        self.resize(1360, 800)
        self.setModal(True)
        self._bouw_interface()
        self._vul_tabel()
        self._update_summary()

    def _bouw_interface(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Recovery Review: {self.recovery_set_name}\n"
            "Kies de juiste Spotify-match en playlistselectie. "
            "Er wordt nog geen playlist gemaakt."
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
        selectie.addSpacing(20)
        selectie.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(FILTERS)
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        selectie.addWidget(self.filter_combo)
        selectie.addWidget(QLabel("Zoeken:"))
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText(
            "Bestandsnaam, artiest, titel, week of positie"
        )
        self.search_field.textChanged.connect(self._apply_filter)
        selectie.addWidget(self.search_field, stretch=1)
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
        self.table.setSortingEnabled(True)
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
            ("year", "Jaar"),
            ("week", "Week"),
            ("position", "Chartpositie"),
            ("artist", "Artiest"),
            ("title", "Titel"),
            ("version", "Versie"),
            ("filename", "Originele bestandsnaam"),
            ("recovery", "Recovery status"),
            ("spotify", "Spotify status"),
            ("matches", "Aantal Spotify matches"),
            ("source", "Bronkeuze"),
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
        kandidaat_acties = QHBoxLayout()
        self.open_spotify_button = QPushButton("Open in Spotify")
        self.open_spotify_button.setEnabled(False)
        self.open_spotify_button.clicked.connect(self._open_spotify)
        kandidaat_acties.addWidget(self.open_spotify_button)
        self.youtube_button = QPushButton("Search YouTube")
        self.youtube_button.clicked.connect(self._search_youtube)
        kandidaat_acties.addWidget(self.youtube_button)
        kandidaat_acties.addStretch(1)
        self.detail_layout.addLayout(kandidaat_acties)
        self.youtube_box = QGroupBox("YouTube")
        youtube_layout = QVBoxLayout(self.youtube_box)
        self.youtube_status_label = QLabel("Nog niet gezocht op YouTube.")
        self.youtube_status_label.setWordWrap(True)
        youtube_layout.addWidget(self.youtube_status_label)
        self.youtube_candidates_layout = QVBoxLayout()
        youtube_layout.addLayout(self.youtube_candidates_layout)
        youtube_actions = QHBoxLayout()
        self.search_again_button = QPushButton("Search Again")
        self.search_again_button.clicked.connect(self._search_youtube)
        self.no_youtube_button = QPushButton("Geen geschikte YouTube-bron")
        self.no_youtube_button.clicked.connect(self._no_youtube_source)
        self.open_youtube_button = QPushButton("Open op YouTube")
        self.open_youtube_button.setEnabled(False)
        self.open_youtube_button.clicked.connect(self._open_youtube)
        youtube_actions.addWidget(self.search_again_button)
        youtube_actions.addWidget(self.no_youtube_button)
        youtube_actions.addWidget(self.open_youtube_button)
        youtube_layout.addLayout(youtube_actions)
        self.detail_layout.addWidget(self.youtube_box)
        self.detail_layout.addStretch(1)
        detail_scroll.setWidget(self.detail_widget)
        splitter.addWidget(detail_scroll)
        splitter.setSizes([880, 480])
        layout.addWidget(splitter, stretch=1)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        onder = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.continue_button = QPushButton("Playlist voorbereiden")
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
        self._building_table = True
        self.table.setSortingEnabled(False)
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
            confidence = max(
                (
                    kandidaat.confidence
                    for kandidaat in item.candidates
                    if kandidaat.id == self._candidate_choice.get(item.id)
                ),
                default=max(
                    (k.confidence for k in item.candidates),
                    default=None,
                ),
            )
            waarden = (
                item.year,
                item.week,
                item.chart_position,
                item.original_filename,
                item.artist,
                item.title,
                item.recovery_status,
                item.spotify_status,
                confidence,
            )
            for column, waarde in enumerate(waarden, 1):
                tekst = (
                    f"{waarde:.0%}" if column == 9 and waarde is not None
                    else None
                )
                tabelitem = SorteerItem(waarde, tekst)
                tabelitem.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                self.table.setItem(row, column, tabelitem)
        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self._building_table = False
        if self.items:
            self.table.selectRow(0)

    def _item_for_row(self, row):
        if row < 0 or self.table.item(row, 0) is None:
            return None
        return self._items_by_id.get(
            self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        )

    def _selected_ids(self):
        return {
            self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState()
            == Qt.CheckState.Checked
        }

    def _candidate_for(self, item_id):
        item = self._items_by_id[item_id]
        kandidaat_id = self._candidate_choice.get(item_id)
        return next(
            (
                kandidaat for kandidaat in item.candidates
                if kandidaat.id == kandidaat_id
            ),
            None,
        )

    def _visible_rows(self):
        return tuple(
            row for row in range(self.table.rowCount())
            if not self.table.isRowHidden(row)
        )

    def _set_all(self, state):
        self.table.blockSignals(True)
        for row in self._visible_rows():
            tabelitem = self.table.item(row, 0)
            tabelitem.setCheckState(state)
            stel_playlist_selectie_in(
                self.database,
                tabelitem.data(Qt.ItemDataRole.UserRole),
                state == Qt.CheckState.Checked,
            )
        self.table.blockSignals(False)
        self._update_summary()

    def _select_all(self):
        self._set_all(Qt.CheckState.Checked)

    def _select_none(self):
        self._set_all(Qt.CheckState.Unchecked)

    def _invert_selection(self):
        self.table.blockSignals(True)
        for row in self._visible_rows():
            tabelitem = self.table.item(row, 0)
            nieuw = (
                Qt.CheckState.Unchecked
                if tabelitem.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            tabelitem.setCheckState(nieuw)
            stel_playlist_selectie_in(
                self.database,
                tabelitem.data(Qt.ItemDataRole.UserRole),
                nieuw == Qt.CheckState.Checked,
            )
        self.table.blockSignals(False)
        self._update_summary()

    def _item_changed(self, tabelitem):
        if self._building_table or tabelitem.column() != 0:
            return
        stel_playlist_selectie_in(
            self.database,
            tabelitem.data(Qt.ItemDataRole.UserRole),
            tabelitem.checkState() == Qt.CheckState.Checked,
        )
        self._update_summary()

    def _matches_filter(self, item, filter_name, search):
        kandidaat = self._candidate_for(item.id)
        klaar = bool(
            item.id in self._selected_ids()
            and kandidaat
            and kandidaat.spotify_uri
        )
        if filter_name == "Klaar voor playlist" and not klaar:
            return False
        if filter_name == "Nog te beoordelen" and (
            kandidaat or item.spotify_status == "Not Found"
        ):
            return False
        if filter_name == "Geen match" and item.candidates:
            return False
        if (
            filter_name == "Low confidence"
            and item.spotify_status != "Low Confidence"
        ):
            return False
        if (
            filter_name == "FFmpeg-fout"
            and item.recovery_status != "FFmpeg failed"
        ):
            return False
        if filter_name == "0-byte" and item.recovery_status != "Zero-byte":
            return False
        if filter_name == "Missing" and item.recovery_status != "Missing":
            return False
        youtube_rows = laad_youtube_kandidaten(self.database, item.id)
        if filter_name == "YouTube nog niet gezocht" and item.youtube_last_searched:
            return False
        if filter_name == "YouTube kandidaten gevonden" and not youtube_rows:
            return False
        if filter_name == "YouTube-bron gekozen" and not item.selected_youtube_url:
            return False
        if (
            filter_name == "Geen geschikte YouTube-bron"
            and item.youtube_review_status != "REVIEWED_NONE"
        ):
            return False
        if filter_name == "Klaar voor toekomstige recovery" and not (
            item.selected_for_playlist
            and item.selected_youtube_url
            and item.youtube_review_status == "SELECTED"
        ):
            return False
        if search:
            haystack = " ".join(str(waarde or "") for waarde in (
                item.original_filename, item.artist, item.title,
                item.week, item.chart_position,
            )).casefold()
            if search.casefold() not in haystack:
                return False
        return True

    def _apply_filter(self, *_args):
        filter_name = self.filter_combo.currentText()
        search = self.search_field.text().strip()
        for row in range(self.table.rowCount()):
            item = self._item_for_row(row)
            self.table.setRowHidden(
                row,
                not self._matches_filter(item, filter_name, search),
            )

    def _update_summary(self):
        selected = self._selected_ids()
        met_match = sum(
            self._candidate_for(item.id) is not None for item in self.items
        )
        klaar = sum(
            item.id in selected
            and (kandidaat := self._candidate_for(item.id)) is not None
            and bool(kandidaat.spotify_uri)
            for item in self.items
        )
        geen_match = sum(not item.candidates for item in self.items)
        nog = sum(
            bool(item.candidates) and self._candidate_for(item.id) is None
            for item in self.items
        )
        self.summary_label.setText(
            f"Totaal recovery-items: {len(self.items)} | "
            f"Aangevinkt: {len(selected)} | "
            f"Met gekozen Spotify-match: {met_match} | "
            f"Klaar voor playlist: {klaar} | "
            f"Nog te beoordelen: {nog} | "
            f"Geen Spotify-match: {geen_match} | "
            f"YouTube gekozen: {sum(bool(i.selected_youtube_url) for i in self.items)} | "
            f"Geen geschikte YouTube-bron: "
            f"{sum(i.youtube_review_status == 'REVIEWED_NONE' for i in self.items)} | "
            f"Klaar voor toekomstige download: "
            f"{sum(i.selected_for_playlist and bool(i.selected_youtube_url) and i.youtube_review_status == 'SELECTED' for i in self.items)}"
        )
        self.continue_button.setEnabled(bool(selected))
        self._apply_filter()

    def _clear_candidates(self):
        while self.candidates_layout.count():
            child = self.candidates_layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.hide()
                widget.setParent(self)
                self._retired_candidate_widgets.append(widget)

    def _toon_selectie(self):
        item = self._item_for_row(self.table.currentRow())
        if item is None:
            return
        waarden = {
            "year": item.year,
            "week": item.week,
            "position": item.chart_position,
            "artist": item.artist,
            "title": item.title,
            "version": item.version,
            "filename": item.original_filename,
            "recovery": item.recovery_status,
            "spotify": item.spotify_status,
            "matches": len(item.candidates),
            "source": (
                "YouTube-bron" if item.preferred_audio_source == "YOUTUBE"
                else "Spotify-identificatie" if item.selected_candidate_id
                else "Nog niet gekozen"
            ),
        }
        for key, waarde in waarden.items():
            self.detail_labels[key].setText(
                "—" if waarde in (None, "") else str(waarde)
            )
        self._clear_candidates()
        groep = QButtonGroup(self)
        groep.setExclusive(True)
        self._candidate_groups[item.id] = groep
        self.open_spotify_button.setEnabled(
            bool(
                (gekozen := self._candidate_for(item.id))
                and gekozen.spotify_url
            )
        )
        # YouTube mag ook bewust als audiobron naast Spotify-identificatie.
        self.youtube_button.setEnabled(True)
        self._toon_youtube_candidates(item)
        if not item.candidates:
            self.candidates_layout.addWidget(QLabel(
                "No Spotify match found."
            ))
            self.youtube_button.setVisible(True)
            return
        self.youtube_button.setVisible(True)
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
            waarschuwingen = (
                "\nWaarschuwing: " + "; ".join(
                    kandidaat.version_warnings
                )
                if kandidaat.version_warnings else ""
            )
            link = kandidaat.spotify_url or "—"
            tekst = QLabel(
                f"{kandidaat.artist} — {kandidaat.title}\n"
                f"Album: {kandidaat.album or '—'} | "
                f"Releasedatum: {kandidaat.release_date or '—'} | "
                f"Duur: {duur}\nPopularity: "
                f"{kandidaat.popularity if kandidaat.popularity is not None else '—'}"
                f" | Confidence: {kandidaat.confidence:.0%}\n"
                f"Spotify: {link}{waarschuwingen}"
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

    def _clear_youtube_candidates(self):
        while self.youtube_candidates_layout.count():
            child = self.youtube_candidates_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _toon_youtube_candidates(self, item):
        self._clear_youtube_candidates()
        rows = laad_youtube_kandidaten(self.database, item.id)
        selected_id = item.selected_youtube_candidate_id
        self.youtube_status_label.setText(
            item.youtube_search_error
            or (f"{len(rows)} kandidaat/kandidaten gevonden."
                if rows else "Nog geen YouTube-kandidaten gevonden.")
        )
        group = QButtonGroup(self.youtube_box)
        group.setExclusive(True)
        for row in rows:
            card = QGroupBox()
            layout = QHBoxLayout(card)
            thumbnail = QLabel("Geen\nthumbnail")
            thumbnail.setFixedSize(96, 72)
            thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(thumbnail)
            radio = QRadioButton()
            radio.setChecked(row["id"] == selected_id)
            radio.toggled.connect(partial(
                self._youtube_candidate_toggled, item.id, row["id"]
            ))
            group.addButton(radio)
            layout.addWidget(radio)
            duration = (
                f"{row['duration_seconds'] // 60}:"
                f"{row['duration_seconds'] % 60:02d}"
                if row["duration_seconds"] else "—"
            )
            warnings = "; ".join(json.loads(row["warnings_json"] or "[]")) or "—"
            label = QLabel(
                f"{row['title']}\nKanaal: {row['channel_name'] or '—'} | "
                f"Duur: {duration} | Publicatie: {row['published_at'] or '—'}\n"
                f"Weergaven: {row['view_count'] if row['view_count'] is not None else '—'} | "
                f"Confidence: {row['confidence']:.0%}\n"
                f"Scores: artiest {row['artist_score']:.0%}, titel {row['title_score']:.0%}, "
                f"versie {row['version_score']:.0%}, duur {row['duration_score']:.0%}, "
                f"kanaal {row['channel_score']:.0%}, straf {row['penalty_score']:.0%}\n"
                f"Waarschuwingen: {warnings}\n{row['youtube_url']}"
            )
            label.setWordWrap(True)
            layout.addWidget(label, stretch=1)
            self.youtube_candidates_layout.addWidget(card)
            if row["thumbnail_url"]:
                reply = self._network.get(QNetworkRequest(
                    QUrl(row["thumbnail_url"])
                ))
                reply.finished.connect(partial(
                    self._cover_loaded, reply, thumbnail
                ))
        self.open_youtube_button.setEnabled(bool(item.selected_youtube_url))

    def _search_youtube(self):
        item = self._item_for_row(self.table.currentRow())
        if item is None or (self.youtube_thread and self.youtube_thread.isRunning()):
            return
        self.youtube_status_label.setText("YouTube wordt doorzocht…")
        self.youtube_button.setEnabled(False)
        self.search_again_button.setEnabled(False)
        thread = QThread(self)
        worker = YouTubeSearchWorker(
            self.database.pad, item.id, YouTubeSearchProvider.from_environment
        )
        thread.setObjectName("YouTubeSearchThread")
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._youtube_search_completed)
        worker.failed.connect(self._youtube_search_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._youtube_thread_finished)
        self.youtube_thread, self.youtube_worker = thread, worker
        thread.start()

    def _youtube_search_completed(self, _rows, selected_missing):
        self._reload_items()
        item = self._item_for_row(self.table.currentRow())
        if item:
            self._toon_youtube_candidates(item)
        if selected_missing:
            QMessageBox.warning(
                self, "YouTube", "De eerder gekozen video staat niet meer in de zoekresultaten; de keuze is behouden."
            )

    def _youtube_search_failed(self, message):
        self.youtube_status_label.setText(message)
        QMessageBox.warning(self, "YouTube zoeken", message)

    def _youtube_thread_finished(self):
        self.youtube_thread = None
        self.youtube_worker = None
        self.search_again_button.setEnabled(True)
        item = self._item_for_row(self.table.currentRow())
        self.youtube_button.setEnabled(bool(item))

    def _reload_items(self):
        self.items = laad_recovery_review(
            self.database, self.recovery_set_id, apply_auto_selection=False
        )
        self._items_by_id = {item.id: item for item in self.items}

    def _youtube_candidate_toggled(self, item_id, candidate_id, checked):
        if not checked:
            return
        selecteer_youtube_kandidaat(self.database, item_id, candidate_id)
        self._reload_items()
        self._update_summary()
        item = self._items_by_id[item_id]
        self.open_youtube_button.setEnabled(bool(item.selected_youtube_url))

    def _no_youtube_source(self):
        item = self._item_for_row(self.table.currentRow())
        if item:
            markeer_geen_youtube_bron(self.database, item.id)
            self._reload_items()
            self._toon_youtube_candidates(self._items_by_id[item.id])
            self._update_summary()

    def _open_youtube(self):
        item = self._item_for_row(self.table.currentRow())
        if item:
            item = self._items_by_id[item.id]
            if item.selected_youtube_url:
                QDesktopServices.openUrl(QUrl(item.selected_youtube_url))

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
        if not checked:
            return
        try:
            selecteer_spotify_kandidaat(
                self.database, item_id, kandidaat_id, "USER_SELECTED"
            )
        except Exception as error:
            QMessageBox.critical(
                self, "Spotify-match", f"Keuze opslaan mislukt: {error}"
            )
            return
        self._candidate_choice[item_id] = kandidaat_id
        self.open_spotify_button.setEnabled(
            bool(self._candidate_for(item_id).spotify_url)
        )
        self._update_summary()

    def _open_spotify(self):
        item = self._item_for_row(self.table.currentRow())
        kandidaat = self._candidate_for(item.id) if item else None
        if kandidaat and kandidaat.spotify_url:
            QDesktopServices.openUrl(QUrl(kandidaat.spotify_url))

    def _gekozen_kandidaten(self):
        return {
            item_id: kandidaat_id
            for item_id, kandidaat_id in self._candidate_choice.items()
            if kandidaat_id is not None
        }

    def _vraag_onvolledige_selectie(self, aantal):
        dialoog = QMessageBox(self)
        dialoog.setWindowTitle("Recovery Review")
        dialoog.setText(
            f"{aantal} geselecteerde nummers hebben nog geen "
            "Spotify-match gekozen."
        )
        terug = dialoog.addButton(
            "Terug naar review", QMessageBox.ButtonRole.RejectRole
        )
        deselecteer = dialoog.addButton(
            "Niet-gematchte items deselecteren en doorgaan",
            QMessageBox.ButtonRole.AcceptRole,
        )
        annuleren = dialoog.addButton(
            "Annuleren", QMessageBox.ButtonRole.DestructiveRole
        )
        dialoog.exec()
        if dialoog.clickedButton() is deselecteer:
            return "deselect"
        if dialoog.clickedButton() is annuleren:
            return "cancel"
        if dialoog.clickedButton() is terug:
            return "back"
        return "back"

    def _continue(self):
        selected = self._selected_ids()
        zonder_match = {
            item_id for item_id in selected
            if not (
                (kandidaat := self._candidate_for(item_id))
                and kandidaat.spotify_uri
            )
        }
        if zonder_match:
            keuze = self._vraag_onvolledige_selectie(len(zonder_match))
            if keuze == "back":
                return
            if keuze == "cancel":
                self.reject()
                return
            self.table.blockSignals(True)
            for row in range(self.table.rowCount()):
                tabelitem = self.table.item(row, 0)
                if (
                    tabelitem.data(Qt.ItemDataRole.UserRole)
                    in zonder_match
                ):
                    tabelitem.setCheckState(Qt.CheckState.Unchecked)
                    stel_playlist_selectie_in(
                        self.database,
                        tabelitem.data(Qt.ItemDataRole.UserRole),
                        False,
                    )
            self.table.blockSignals(False)
            selected -= zonder_match
        try:
            bewaar_recovery_review(
                self.database,
                self.recovery_set_id,
                selected,
                self._gekozen_kandidaten(),
            )
        except Exception as error:
            QMessageBox.critical(
                self, "Recovery Review", f"Review opslaan mislukt: {error}"
            )
            return
        counts = self._playlist_counts()
        action, playlist_name = self._confirm_playlist(counts)
        if action == "back":
            return
        if action == "cancel":
            self.reject()
            return
        self.playlist_requested = True
        self.prepared_playlist_name = playlist_name
        self.accept()

    def _confirm_playlist(self, counts):
        bevestiging = PlaylistConfirmationDialog(
            counts, self.recovery_set_name, self
        )
        bevestiging.exec()
        return bevestiging.action, bevestiging.playlist_name

    def _playlist_counts(self):
        selected = self._selected_ids()
        with_match = sum(
            self._candidate_for(item.id) is not None for item in self.items
        )
        ready = sum(
            item.id in selected
            and (candidate := self._candidate_for(item.id)) is not None
            and bool(candidate.spotify_uri)
            for item in self.items
        )
        return {
            "total": len(self.items),
            "selected": len(selected),
            "with_match": with_match,
            "ready": ready,
            "without_match": len(selected) - ready,
            "deselected": len(self.items) - len(selected),
        }

    def _back(self):
        self.done(2)

    def closeEvent(self, event):
        if self.youtube_thread and self.youtube_thread.isRunning():
            QMessageBox.information(
                self, "YouTube zoeken",
                "De YouTube-zoekopdracht wordt nog uitgevoerd. Wacht tot deze klaar is.",
            )
            event.ignore()
            return
        self.database.sluit()
        super().closeEvent(event)

    def done(self, result):
        self.database.sluit()
        super().done(result)
