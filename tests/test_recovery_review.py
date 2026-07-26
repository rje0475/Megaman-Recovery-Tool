import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication

from core.recovery_review import (
    bewaar_recovery_review,
    laad_recovery_review,
    selecteer_spotify_kandidaat,
    versie_waarschuwingen,
)
from database import maak_database, verkrijg_of_maak_recovery_set
from gui.recovery_review import (
    PlaylistConfirmationDialog,
    PlaylistResultDialog,
    RecoveryReviewDialog,
)
from gui.workers import YouTubeSearchWorker
from core.youtube.models import YouTubeVideo


class RecoveryReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "review.db"
        self.db = maak_database(self.path)
        self.set_id = verkrijg_of_maak_recovery_set(
            self.db, "Jaarcollectie.part01.rar"
        )
        self.matched = self._item(
            "07050090 Delain - Frozen.mp3",
            "Delain", "Frozen", "corrupt", "ffmpeg,salvage",
            spotify_status="MATCHED",
        )
        self.missing = self._item(
            "2007/07280058 Natasha Bedingfield - Soulmate.mp3",
            "Natasha Bedingfield", "Soulmate", "ontbreekt", "salvage",
            missing=1, spotify_status="NOT_FOUND",
        )
        self.zero = self._item(
            "2007/07290085 Kelly Rowland feat. Eve - Like This.mp3",
            "Kelly Rowland feat. Eve", "Like This",
            "nul_bytes", "salvage", zero=1,
            spotify_status="LOW_CONFIDENCE",
        )
        self.candidate_ids = (
            self._candidate(self.matched, "one", 0.98),
            self._candidate(self.matched, "two", 0.91),
        )
        self.db.verbinding.commit()

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def _item(
        self, path, artist, title, probleem_type, probleem_bron,
        missing=0, zero=0, spotify_status=None,
    ):
        cursor = self.db.verbinding.execute(
            """
            INSERT INTO recovery_items (
              rar_set_key, recovery_set_id, verwacht_rel_pad,
              verwacht_rel_pad_norm, probleem_type, probleem_bron,
              feit_ontbreekt, feit_nul_bytes, feit_corrupt,
              spotify_verwerkt, download_verwerkt, geplaatst,
              bepaalde_artiest, bepaalde_titel, spotify_status,
              aangemaakt_op, bijgewerkt_op
            ) VALUES (
              'jaarcollectie', ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0,
              ?, ?, ?, 'nu', 'nu'
            )
            """,
            (
                self.set_id, path, path.casefold(),
                probleem_type, probleem_bron, missing, zero,
                artist, title, spotify_status,
            ),
        )
        return cursor.lastrowid

    def _candidate(
        self, item_id, track_id, score, selected=0,
        artist="Delain", title="Frozen",
    ):
        cursor = self.db.verbinding.execute(
            """
            INSERT INTO spotify_candidates (
              recovery_item_id, spotify_track_id, spotify_uri, spotify_url,
              artist, title, album, album_cover_url, duration_ms, popularity,
              release_date,
              total_score, artist_score, title_score, version_score,
              duration_score, rank_number, search_strategy, search_query,
              selected, rejected, score_reason
            ) VALUES (
              ?, ?, ?, ?, ?, ?, 'Lucidity', NULL,
              240000, 70, '2006-09-04', ?, .99, .99, 1, 1, 1,
              'FIELD_FILTERS', 'query', ?, 0, 'goed'
            )
            """,
            (
                item_id, track_id, f"spotify:track:{track_id}",
                f"https://open.spotify.com/track/{track_id}",
                artist, title, score, selected,
            ),
        )
        return cursor.lastrowid

    def test_review_laadt_alleen_definitieve_setitems_en_chartgegevens(self):
        andere_set = verkrijg_of_maak_recovery_set(
            self.db, "Andere.part01.rar"
        )
        self.db.verbinding.execute(
            "UPDATE recovery_items SET recovery_set_id=? WHERE id=?",
            (andere_set, self.zero),
        )
        self.db.verbinding.commit()
        items = laad_recovery_review(self.db, self.set_id)
        self.assertEqual(len(items), 2)
        delain = next(item for item in items if item.id == self.matched)
        self.assertEqual(
            (delain.year, delain.week, delain.chart_position),
            (2007, 5, 90),
        )
        self.assertEqual(delain.recovery_status, "FFmpeg failed")
        self.assertEqual(delain.spotify_status, "Match")
        self.assertEqual(len(delain.candidates), 2)
        ontbrekend = next(item for item in items if item.id == self.missing)
        self.assertEqual(ontbrekend.recovery_status, "Missing")
        self.assertEqual(ontbrekend.spotify_status, "Not Found")

    def test_reviewselectie_en_kandidaat_worden_opgeslagen(self):
        bewaar_recovery_review(
            self.db,
            self.set_id,
            {self.matched, self.zero},
            {self.matched: self.candidate_ids[1]},
        )
        geselecteerd = {
            rij["id"]
            for rij in self.db.verbinding.execute(
                """
                SELECT id FROM recovery_items
                WHERE playlist_selected=1
                """
            )
        }
        self.assertEqual(geselecteerd, {self.matched, self.zero})
        kandidaat = self.db.verbinding.execute(
            """
            SELECT spotify_track_id FROM spotify_candidates
            WHERE recovery_item_id=? AND selected=1
            """,
            (self.matched,),
        ).fetchone()
        self.assertEqual(kandidaat["spotify_track_id"], "two")

    def test_wizard_selectie_detail_en_continue(self):
        dialog = RecoveryReviewDialog(
            self.set_id,
            "Jaarcollectie",
            database_factory=maak_database,
            database_path=self.path,
        )
        try:
            self.assertEqual(dialog.table.rowCount(), 3)
            self.assertFalse(dialog.continue_button.isEnabled())
            dialog._select_all()
            self.assertTrue(dialog.continue_button.isEnabled())
            self.assertIn(
                "Aangevinkt: 3",
                dialog.summary_label.text(),
            )
            dialog._invert_selection()
            self.assertFalse(dialog.continue_button.isEnabled())
            dialog._select_none()
            self.assertFalse(dialog.continue_button.isEnabled())

            missing_row = next(
                row for row in range(dialog.table.rowCount())
                if dialog.table.item(row, 0).data(
                    Qt.ItemDataRole.UserRole
                ) == self.missing
            )
            dialog.table.selectRow(missing_row)
            QApplication.processEvents()
            self.assertEqual(
                dialog.detail_labels["matches"].text(), "0"
            )
            self.assertFalse(dialog.youtube_button.isHidden())
            self.assertTrue(dialog.youtube_button.isEnabled())
        finally:
            dialog.reject()

    def test_hoge_confidence_wordt_automatisch_gekozen(self):
        items = laad_recovery_review(self.db, self.set_id)
        item = next(item for item in items if item.id == self.matched)
        self.assertEqual(item.selected_candidate_id, self.candidate_ids[0])
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_items WHERE id=?", (self.matched,)
        ).fetchone()
        self.assertEqual(rij["match_review_status"], "AUTO_SELECTED")
        self.assertEqual(rij["selected_spotify_uri"], "spotify:track:one")

    def test_low_confidence_en_bijna_gelijke_matches_niet_auto(self):
        low_candidate = self._candidate(
            self.zero, "low", 0.93,
            artist="Kelly Rowland", title="Like This",
        )
        tweede = self._candidate(
            self.missing, "close-1", 0.98,
            artist="Natasha Bedingfield", title="Soulmate",
        )
        self._candidate(
            self.missing, "close-2", 0.97,
            artist="Natasha Bedingfield", title="Soulmate",
        )
        self.db.verbinding.execute(
            "UPDATE recovery_items SET spotify_status='MATCHED' WHERE id=?",
            (self.missing,),
        )
        self.db.verbinding.commit()
        items = {
            item.id: item
            for item in laad_recovery_review(self.db, self.set_id)
        }
        self.assertIsNone(items[self.zero].selected_candidate_id)
        self.assertIsNone(items[self.missing].selected_candidate_id)
        self.assertIsNotNone(low_candidate)
        self.assertIsNotNone(tweede)

    def test_handmatige_keuze_blijft_na_heropenen_bewaard(self):
        selecteer_spotify_kandidaat(
            self.db, self.matched, self.candidate_ids[1]
        )
        eerste = laad_recovery_review(self.db, self.set_id)
        tweede = laad_recovery_review(self.db, self.set_id)
        for items in (eerste, tweede):
            item = next(item for item in items if item.id == self.matched)
            self.assertEqual(
                item.selected_candidate_id, self.candidate_ids[1]
            )
            self.assertEqual(item.match_review_status, "USER_SELECTED")

    def test_checkbox_blijft_na_heropenen_bewaard(self):
        dialog = RecoveryReviewDialog(
            self.set_id, "Jaarcollectie",
            database_factory=maak_database,
            database_path=self.path,
        )
        row = next(
            row for row in range(dialog.table.rowCount())
            if dialog._item_for_row(row).id == self.matched
        )
        dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        dialog.reject()
        heropend = RecoveryReviewDialog(
            self.set_id, "Jaarcollectie",
            database_factory=maak_database,
            database_path=self.path,
        )
        try:
            row = next(
                row for row in range(heropend.table.rowCount())
                if heropend._item_for_row(row).id == self.matched
            )
            self.assertEqual(
                heropend.table.item(row, 0).checkState(),
                Qt.CheckState.Checked,
            )
        finally:
            heropend.reject()

    def test_versieconflicten_worden_herkend(self):
        self.assertIn(
            "Versie wijkt mogelijk af",
            versie_waarschuwingen(
                "Artist - Track (Radio Edit).mp3",
                "Track",
            ),
        )
        self.assertIn(
            "Live-versie",
            versie_waarschuwingen("Artist - Track.mp3", "Track Live"),
        )
        self.assertIn(
            "Andere remix mogelijk",
            versie_waarschuwingen(
                "Track (DJ One Remix).mp3",
                "Track (DJ Two Remix)",
            ),
        )
        self.assertNotIn(
            "Andere remix mogelijk",
            versie_waarschuwingen(
                "Artist - Track (DJ One Remix).mp3",
                "Track (DJ One Remix)",
            ),
        )

    def test_filters_zoeken_sorteren_en_realtime_tellingen(self):
        dialog = RecoveryReviewDialog(
            self.set_id, "Jaarcollectie",
            database_factory=maak_database,
            database_path=self.path,
        )
        try:
            dialog.search_field.setText("Natasha")
            zichtbaar = [
                dialog._item_for_row(row).id
                for row in dialog._visible_rows()
            ]
            self.assertEqual(zichtbaar, [self.missing])
            dialog.search_field.clear()
            dialog.filter_combo.setCurrentText("FFmpeg-fout")
            self.assertEqual(
                [
                    dialog._item_for_row(row).id
                    for row in dialog._visible_rows()
                ],
                [self.matched],
            )
            dialog.filter_combo.setCurrentText("Alles")
            dialog.table.sortItems(
                3, Qt.SortOrder.DescendingOrder
            )
            posities = [
                dialog._item_for_row(row).chart_position
                for row in range(dialog.table.rowCount())
            ]
            self.assertEqual(posities, sorted(posities, reverse=True))
            dialog._select_all()
            self.assertIn("Aangevinkt: 3", dialog.summary_label.text())
            self.assertIn(
                "Klaar voor playlist: 1", dialog.summary_label.text()
            )
        finally:
            dialog.reject()

    def test_continue_valideert_en_kan_ongematchte_deselecteren(self):
        dialog = RecoveryReviewDialog(
            self.set_id, "Jaarcollectie",
            database_factory=maak_database,
            database_path=self.path,
        )
        dialog._select_all()
        dialog._vraag_onvolledige_selectie = lambda aantal: (
            "deselect" if aantal == 2 else "back"
        )
        dialog._confirm_playlist = lambda counts: ("create", "Jaarcollectie")
        dialog._continue()
        controle = maak_database(self.path)
        try:
            selected = controle.verbinding.execute(
                """
                SELECT COUNT(*) aantal FROM recovery_items
                WHERE recovery_set_id=? AND playlist_selected=1
                """,
                (self.set_id,),
            ).fetchone()["aantal"]
            self.assertEqual(selected, 1)
            self.assertIsNone(
                controle.verbinding.execute(
                    "SELECT spotify_playlist_id FROM recovery_sets WHERE id=?",
                    (self.set_id,),
                ).fetchone()["spotify_playlist_id"]
            )
        finally:
            controle.sluit()

    def test_migraties_bevatten_reviewkolommen(self):
        recovery_columns = {
            rij["name"]
            for rij in self.db.verbinding.execute(
                "PRAGMA table_info(recovery_items)"
            )
        }
        self.assertTrue({
            "selected_spotify_candidate_id",
            "selected_spotify_uri",
            "selected_spotify_track_id",
            "selected_spotify_artist",
            "selected_spotify_title",
            "selected_spotify_album",
            "selected_spotify_duration_ms",
            "selected_spotify_confidence",
            "match_review_status",
            "match_reviewed_at",
        } <= recovery_columns)
        candidate_columns = {
            rij["name"]
            for rij in self.db.verbinding.execute(
                "PRAGMA table_info(spotify_candidates)"
            )
        }
        self.assertIn("release_date", candidate_columns)

    def test_playlist_voorbereiden_slaat_keuze_op_zonder_api_actie(self):
        dialog = RecoveryReviewDialog(
            self.set_id,
            "Jaarcollectie",
            database_factory=maak_database,
            database_path=self.path,
        )
        row = next(
            row for row in range(dialog.table.rowCount())
            if dialog._item_for_row(row).id == self.matched
        )
        dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        dialog._confirm_playlist = lambda counts: ("create", "Mijn playlist")
        dialog._continue()
        self.assertTrue(dialog.playlist_requested)
        self.assertEqual(dialog.prepared_playlist_name, "Mijn playlist")
        controle = maak_database(self.path)
        try:
            aantal = controle.verbinding.execute(
                """
                SELECT COUNT(*) aantal FROM recovery_items
                WHERE recovery_set_id=? AND playlist_selected=1
                """,
                (self.set_id,),
            ).fetchone()["aantal"]
            self.assertEqual(aantal, 1)
            recovery_set = controle.verbinding.execute(
                "SELECT * FROM recovery_sets WHERE id=?",
                (self.set_id,),
            ).fetchone()
            self.assertIsNone(recovery_set["spotify_playlist_id"])
        finally:
            controle.sluit()

    def test_annuleren_maakt_geen_playlist(self):
        dialog = RecoveryReviewDialog(
            self.set_id, "Jaarcollectie",
            database_factory=maak_database, database_path=self.path,
        )
        row = next(
            row for row in range(dialog.table.rowCount())
            if dialog._item_for_row(row).id == self.matched
        )
        dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        dialog._confirm_playlist = lambda counts: ("cancel", "Jaarcollectie")
        dialog._continue()
        self.assertFalse(dialog.playlist_requested)
        rij = self.db.verbinding.execute(
            "SELECT spotify_playlist_id FROM recovery_sets WHERE id=?",
            (self.set_id,),
        ).fetchone()
        self.assertIsNone(rij["spotify_playlist_id"])

    def test_resultaatscherm_toont_en_opent_echte_urls(self):
        summary = {
            "playlist_name": "Jaarcollectie",
            "unique_selected": 10,
            "playlist_existing": 3,
            "playlist_added": 7,
            "duplicates_skipped": 2,
            "unmatched_selected": 1,
            "playlist_url": "https://open.spotify.com/playlist/echt-id",
            "playlist_sync_status": "SUCCESS",
        }
        dialog = PlaylistResultDialog(
            summary, self.path.parent / "rapport.txt"
        )
        try:
            self.assertIn("Nieuw toegevoegd: 7", dialog.result_label.text())
            with patch(
                "gui.recovery_review.QDesktopServices.openUrl"
            ) as openen:
                dialog._open_playlist()
            self.assertEqual(
                openen.call_args.args[0].toString(),
                "https://open.spotify.com/playlist/echt-id",
            )
        finally:
            dialog.reject()

    def test_bevestigingsdialoog_toont_tellingen_en_bewerkbare_naam(self):
        counts = {
            "total": 24, "selected": 20, "with_match": 18,
            "ready": 17, "without_match": 3, "deselected": 4,
        }
        dialog = PlaylistConfirmationDialog(
            counts, "Jaarcollectie"
        )
        try:
            tekst = dialog.findChildren(type(dialog.name_field))[0]
            self.assertEqual(tekst.text(), "Jaarcollectie")
            dialog.name_field.setText("Mijn herstelplaylist")
            dialog._finish("create")
            self.assertEqual(dialog.action, "create")
            self.assertEqual(dialog.playlist_name, "Mijn herstelplaylist")
        finally:
            dialog.reject()

    def test_youtube_filter_selectie_tellingen_en_echte_url(self):
        cursor = self.db.verbinding.execute(
            """INSERT INTO youtube_candidates(
            recovery_item_id,video_id,youtube_url,title,channel_name,
            confidence,artist_score,title_score,version_score,duration_score,
            channel_score,penalty_score,warnings_json,raw_metadata_json,
            search_query,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.missing, "vid1", "https://www.youtube.com/watch?v=vid1",
             "Soulmate", "Natasha - Topic", .97, 1, 1, 1, 1, 1, 0,
             "[]", "{}", "Natasha Soulmate", "nu", "nu"),
        )
        self.db.verbinding.execute(
            "UPDATE recovery_items SET youtube_last_searched='nu' WHERE id=?",
            (self.missing,),
        )
        self.db.verbinding.commit()
        dialog = RecoveryReviewDialog(
            self.set_id, "Jaarcollectie",
            database_factory=maak_database, database_path=self.path,
        )
        try:
            dialog._youtube_candidate_toggled(
                self.missing, cursor.lastrowid, True
            )
            self.assertIn("YouTube gekozen: 1", dialog.summary_label.text())
            dialog.filter_combo.setCurrentText("YouTube-bron gekozen")
            self.assertEqual(len(dialog._visible_rows()), 1)
            row = dialog._visible_rows()[0]
            dialog.table.selectRow(row)
            QApplication.processEvents()
            with patch("gui.recovery_review.QDesktopServices.openUrl") as opened:
                dialog._open_youtube()
            self.assertEqual(
                opened.call_args.args[0].toString(),
                "https://www.youtube.com/watch?v=vid1",
            )
        finally:
            dialog.reject()

    def test_youtube_geen_bron_blijft_persistent(self):
        dialog = RecoveryReviewDialog(
            self.set_id, "Jaarcollectie",
            database_factory=maak_database, database_path=self.path,
        )
        try:
            row = next(
                row for row in range(dialog.table.rowCount())
                if dialog._item_for_row(row).id == self.missing
            )
            dialog.table.selectRow(row)
            dialog._no_youtube_source()
            reopened = laad_recovery_review(
                dialog.database, self.set_id, apply_auto_selection=False
            )
            item = next(item for item in reopened if item.id == self.missing)
            self.assertEqual(item.youtube_review_status, "REVIEWED_NONE")
        finally:
            dialog.reject()

    def test_youtube_worker_draait_in_thread_en_ruimt_op(self):
        class Provider:
            def search(_self, _query, limit=10):
                return (YouTubeVideo(
                    "vid", "https://www.youtube.com/watch?v=vid",
                    "Natasha Bedingfield Soulmate", "Natasha - Topic", 240,
                ),)

        thread = QThread()
        worker = YouTubeSearchWorker(
            self.path, self.missing, lambda: Provider()
        )
        worker.moveToThread(thread)
        loop = QEventLoop()
        results = []
        worker.completed.connect(lambda rows, missing: results.append(rows))
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.finished.connect(loop.quit)
        thread.start()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        thread.wait(1000)
        self.assertFalse(thread.isRunning())
        self.assertTrue(results)


if __name__ == "__main__":
    unittest.main()
