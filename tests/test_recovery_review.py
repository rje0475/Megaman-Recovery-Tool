import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.recovery_review import (
    bewaar_recovery_review,
    laad_recovery_review,
)
from database import maak_database, verkrijg_of_maak_recovery_set
from gui.recovery_review import RecoveryReviewDialog


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
            self._candidate(self.matched, "one", 0.98, selected=1),
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

    def _candidate(self, item_id, track_id, score, selected=0):
        cursor = self.db.verbinding.execute(
            """
            INSERT INTO spotify_candidates (
              recovery_item_id, spotify_track_id, spotify_uri, spotify_url,
              artist, title, album, album_cover_url, duration_ms, popularity,
              total_score, artist_score, title_score, version_score,
              duration_score, rank_number, search_strategy, search_query,
              selected, rejected, score_reason
            ) VALUES (
              ?, ?, ?, ?, 'Delain', 'Frozen', 'Lucidity', NULL,
              240000, 70, ?, .99, .99, 1, 1, 1,
              'FIELD_FILTERS', 'query', ?, 0, 'goed'
            )
            """,
            (
                item_id, track_id, f"spotify:track:{track_id}",
                f"https://open.spotify.com/track/{track_id}",
                score, selected,
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
                "Selected for playlist: 3",
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
            self.assertFalse(dialog.youtube_button.isEnabled())
        finally:
            dialog.reject()

    def test_continue_slaat_op_zonder_playlistactie(self):
        dialog = RecoveryReviewDialog(
            self.set_id,
            "Jaarcollectie",
            database_factory=maak_database,
            database_path=self.path,
        )
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog._continue()
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


if __name__ == "__main__":
    unittest.main()
