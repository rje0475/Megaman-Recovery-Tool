import io
import json
import tempfile
import unittest
from pathlib import Path

from core.spotify.models import (
    LOW_CONFIDENCE,
    MANUAL_REVIEW,
    MATCHED,
    NOT_FOUND,
    SpotifyTrack,
)
from core.spotify.scoring import (
    bereken_score,
    normaliseer_artiest,
    normaliseer_tekst,
    score_track,
)
from core.spotify.parsing import parseer_recovery_itemnaam
from core.spotify.search import (
    SpotifyRecoverySetError,
    beschikbare_recovery_sets,
    voer_spotify_search_uit,
    zoek_beste_match,
)
from database import (
    leid_archive_set_name_af,
    maak_database,
    verkrijg_of_maak_recovery_set,
)


class FakeSpotifyClient:
    def __init__(self, resultaten=None):
        self.resultaten = resultaten or {}
        self.queries = []

    def search_tracks(self, query, limit=20):
        self.queries.append((query, limit))
        return tuple(self.resultaten.get(query, ()))


def track(
    track_id, artist, title, duration=180000, popularity=50,
    extra_artists=(),
):
    return SpotifyTrack(
        track_id, f"spotify:track:{track_id}",
        f"https://open.spotify.com/track/{track_id}", "Album",
        (artist, *extra_artists), title, duration, popularity,
    )


class SpotifyScoringTest(unittest.TestCase):
    def test_hitlijstcodes_worden_centraal_voor_query_en_matching_verwijderd(self):
        gevallen = (
            (
                "07050090 Delain - Frozen",
                "Delain", "Frozen", "07050090",
            ),
            (
                "07280058 Natasha Bedingfield - Soulmate",
                "Natasha Bedingfield", "Soulmate", "07280058",
            ),
            (
                "07110066 30 Seconds To Mars - The Kill (Bury Me)",
                "30 Seconds To Mars", "The Kill (Bury Me)", "07110066",
            ),
            (
                "07470009 Jeroen Van Der Boom - Een Wereld (Radio Edit)",
                "Jeroen Van Der Boom", "Een Wereld (Radio Edit)",
                "07470009",
            ),
        )
        for origineel, artiest, titel, code in gevallen:
            with self.subTest(origineel=origineel):
                parsed = parseer_recovery_itemnaam(
                    origineel, f"{code} {artiest}", titel
                )
                self.assertEqual(parsed.chart_code, code)
                self.assertEqual(parsed.artist, artiest)
                self.assertEqual(parsed.title, titel)
                self.assertEqual(
                    parsed.free_query, f"{artiest} {titel}"
                )
                client = FakeSpotifyClient()
                match = zoek_beste_match(
                    client, f"{code} {artiest}", titel
                )
                self.assertEqual(match.status, NOT_FOUND)
                self.assertEqual(
                    client.queries[1][0], f"{artiest} {titel}"
                )
                self.assertTrue(
                    all(code not in query for query, _ in client.queries)
                )

    def test_cijfers_worden_niet_middenin_of_als_jaartal_verwijderd(self):
        gevallen = (
            "Prince - 1999",
            "U2 - One",
            "2007 - Track",
            "Artist - 07050090 Reasons",
            "30 Seconds To Mars - The Kill",
        )
        for naam in gevallen:
            with self.subTest(naam=naam):
                parsed = parseer_recovery_itemnaam(naam)
                self.assertIsNone(parsed.chart_code)

    def test_normalisatie_en_duur_beinvloeden_score(self):
        self.assertEqual(
            normaliseer_tekst("Beyoncé feat. Jay-Z"),
            "beyonce jay z",
        )
        exact = score_track(
            "Artist", "Song", 180000,
            track("exact", "Artist", "Song", 180000),
        )
        afwijkend = score_track(
            "Artist", "Song", 180000,
            track("ander", "Other", "Different", 240000),
        )
        self.assertGreater(exact, afwijkend)

    def test_numerieke_artiestalias(self):
        self.assertEqual(
            normaliseer_artiest("30 Seconds To Mars"),
            normaliseer_artiest("Thirty Seconds To Mars"),
        )
        score = bereken_score(
            "30 Seconds To Mars", "The Kill", None,
            track("mars", "Thirty Seconds To Mars", "The Kill"),
        )
        self.assertFalse(score.rejected)
        self.assertEqual(score.total, 1.0)

    def test_extra_artiest_en_feat_worden_correct_vergeleken(self):
        score = bereken_score(
            "Bob Sinclar feat. Cutee B", "Sound Of Freedom", None,
            track(
                "bob", "Bob Sinclar", "Sound Of Freedom",
                extra_artists=("Cutee B",),
            ),
        )
        self.assertFalse(score.rejected)
        self.assertEqual(score.primary_artist, 1.0)
        self.assertEqual(score.extra_artists, 1.0)

    def test_praktijkvarianten_worden_als_dezelfde_track_herkend(self):
        gevallen = (
            (
                "Jeroen Van Der Boom", "Een Wereld (Radio Edit)",
                ("Jeroen Van Der Boom",), "Een Wereld - Radio Edit",
            ),
            (
                "Enrique Iglesias", "Tired Of Being Sorry (Radio Edit)",
                ("Enrique Iglesias",), "Tired Of Being Sorry",
            ),
            (
                "30 Seconds To Mars", "The Kill",
                ("Thirty Seconds To Mars",), "The Kill",
            ),
            (
                "Nice 2 Meet", "Divina Conchita",
                ("Nice2Meet",), "Divina Conchita",
            ),
            (
                "Kelly Rowland feat. Eve", "Like This",
                ("Kelly Rowland", "Eve"), "Like This",
            ),
        )
        for artiest, titel, spotify_artiesten, spotify_titel in gevallen:
            with self.subTest(artiest=artiest, titel=titel):
                kandidaat = SpotifyTrack(
                    "praktijk", "spotify:track:praktijk",
                    "https://open.spotify.com/track/praktijk", "Album",
                    spotify_artiesten, spotify_titel, 180000, 50,
                )
                score = bereken_score(
                    artiest, titel, 180000, kandidaat
                )
                self.assertFalse(score.rejected)
                self.assertGreaterEqual(score.total, .95)

    def test_edit_versies_accenten_en_apostroffen_normaliseren(self):
        self.assertEqual(
            normaliseer_tekst("Één Melodie (Radio Edit)"),
            normaliseer_tekst("Een Melodie"),
        )
        self.assertEqual(
            normaliseer_tekst("Track - Single Edit"),
            normaliseer_tekst("Track"),
        )
        self.assertEqual(
            normaliseer_tekst("Everybody’s Free"),
            normaliseer_tekst("Everybody's Free"),
        )

    def test_verkeerde_primaire_artiesten_worden_hard_afgewezen(self):
        gevallen = (
            ("Delain", "Madonna", "Frozen"),
            ("All-Music", "Lil Kleine", "Goud"),
            ("Eric Van Kleef feat. Boogshe", "Banned Vinyl", "My Ass"),
        )
        for lokaal, spotify, titel in gevallen:
            with self.subTest(lokaal=lokaal, spotify=spotify):
                score = bereken_score(
                    lokaal, titel, None,
                    track("fout", spotify, titel),
                )
                self.assertTrue(score.rejected)
                self.assertEqual(score.total, 0.0)

    def test_hard_afgewezen_kandidaat_wordt_not_found(self):
        client = FakeSpotifyClient({
            'artist:"Delain" track:"Frozen"': (
                track("madonna", "Madonna", "Frozen"),
            ),
        })
        with self.assertLogs("core.spotify.search", level="DEBUG") as logs:
            match = zoek_beste_match(client, "Delain", "Frozen")
        self.assertEqual(match.status, NOT_FOUND)
        self.assertIsNone(match.track)
        self.assertIn("afgewezen", "\n".join(logs.output))

    def test_drie_strategieen_en_hoogste_score_wordt_gekozen(self):
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("laag", "Other", "Song"),
            ),
            "Artist Song": (track("beste", "Artist", "Song"),),
            "Song": (track("midden", "Artist", "Song Live"),),
        })
        match = zoek_beste_match(client, "Artist", "Song", 180000)
        self.assertEqual(
            [query for query, _ in client.queries],
            [
                'artist:"Artist" track:"Song"',
                "Artist Song",
                "Song",
            ],
        )
        self.assertEqual(match.track.track_id, "beste")
        self.assertEqual(match.status, MATCHED)
        self.assertEqual(match.search_method, "ARTIST_TITLE")


class SpotifySearchEngineDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = maak_database(Path(self.temp.name) / "test.db")
        self.set_id = verkrijg_of_maak_recovery_set(
            self.db, "Current.part01.rar"
        )

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def _item(
        self, artist, title, path="set/song.mp3", recovery_set_id=None
    ):
        now = "2026-01-01T00:00:00"
        cursor = self.db.verbinding.execute(
            """
            INSERT INTO recovery_items (
              rar_set_key, recovery_set_id,
              verwacht_rel_pad, verwacht_rel_pad_norm,
              probleem_type, probleem_bron, feit_ontbreekt,
              spotify_verwerkt, download_verwerkt, geplaatst,
              bepaalde_artiest, bepaalde_titel, aangemaakt_op, bijgewerkt_op
            ) VALUES ('set', ?, ?, ?, 'ontbreekt', 'salvage', 1, 0, 0, 0,
                      ?, ?, ?, ?)
            """,
            (
                recovery_set_id or self.set_id,
                path, path.casefold(), artist, title, now, now,
            ),
        )
        self.db.verbinding.commit()
        return cursor.lastrowid

    def test_recovery_set_wordt_uit_hoofdarchive_afgeleid(self):
        self.assertEqual(
            leid_archive_set_name_af("Andere Naam.part001.rar"),
            "Andere Naam",
        )
        set_id = verkrijg_of_maak_recovery_set(
            self.db, "Andere Naam.part001.rar"
        )
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_sets WHERE id=?", (set_id,)
        ).fetchone()
        self.assertEqual(rij["archive_name"], "Andere Naam.part001.rar")
        self.assertEqual(rij["archive_set_name"], "Andere Naam")

    def test_zonder_geldige_set_wordt_niet_gestart(self):
        leeg = maak_database(Path(self.temp.name) / "leeg.db")
        try:
            with self.assertRaisesRegex(
                SpotifyRecoverySetError, "Geen geldige recovery-set"
            ):
                voer_spotify_search_uit(
                    leeg, client=FakeSpotifyClient(),
                    uitvoer=io.StringIO(),
                )
        finally:
            leeg.sluit()

    def test_match_wordt_volledig_op_recovery_item_opgeslagen(self):
        item_id = self._item("Artist", "Song")
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("id-1", "Artist", "Song", popularity=88),
            )
        })
        log = io.StringIO()
        summary = voer_spotify_search_uit(
            self.db, client=client, uitvoer=log
        )
        rij = self.db.verbinding.execute(
            "SELECT * FROM recovery_items WHERE id=?", (item_id,)
        ).fetchone()
        self.assertEqual(summary.matched, 1)
        self.assertEqual(rij["spotify_track_id"], "id-1")
        self.assertEqual(rij["spotify_uri"], "spotify:track:id-1")
        self.assertEqual(rij["spotify_status"], MATCHED)
        self.assertEqual(rij["spotify_popularity"], 88)
        self.assertEqual(json.loads(rij["spotify_artists"]), ["Artist"])
        self.assertGreater(rij["spotify_confidence"], .85)
        self.assertIn("Confidence:", log.getvalue())
        self.assertEqual(summary.recovery_set_id, self.set_id)

    def test_not_found_low_confidence_en_manual_review_worden_bewaard(self):
        not_found = self._item("Nobody", "Missing", "set/missing.mp3")
        low = self._item("Artist", "Song", "set/low.mp3")
        manual = self._item(None, "Onbekend", "set/manual.mp3")
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("weak", "Artist", "Song 2"),
            )
        })
        summary = voer_spotify_search_uit(
            self.db, client=client, uitvoer=io.StringIO()
        )
        statuses = {
            rij["id"]: rij["spotify_status"]
            for rij in self.db.verbinding.execute(
                "SELECT id, spotify_status FROM recovery_items"
            )
        }
        self.assertEqual(statuses[not_found], NOT_FOUND)
        self.assertEqual(statuses[low], LOW_CONFIDENCE)
        self.assertEqual(statuses[manual], MANUAL_REVIEW)
        self.assertEqual(
            (summary.not_found, summary.low_confidence, summary.manual_review),
            (1, 1, 1),
        )

    def test_bestaande_handmatige_status_wordt_niet_overschreven(self):
        item_id = self._item("Artist", "Song")
        self.db.verbinding.execute(
            """
            INSERT INTO spotify_smart_results (
              recovery_item_id, local_path, status, checked_at, reason
            ) VALUES (?, 'set/song.mp3', 'MANUAL', 'nu', 'handmatig')
            """,
            (item_id,),
        )
        self.db.verbinding.commit()
        client = FakeSpotifyClient({
            'artist:"Artist" track:"Song"': (
                track("id-1", "Artist", "Song"),
            )
        })
        summary = voer_spotify_search_uit(
            self.db, client=client, uitvoer=io.StringIO()
        )
        rij = self.db.verbinding.execute(
            "SELECT spotify_status FROM recovery_items WHERE id=?",
            (item_id,),
        ).fetchone()
        self.assertEqual(summary.skipped_manual, 1)
        self.assertEqual(client.queries, [])
        self.assertIsNone(rij["spotify_status"])

    def test_explicit_selecteren_op_id_en_naam_mengt_sets_niet(self):
        andere_id = verkrijg_of_maak_recovery_set(
            self.db, "Andere.part01.rar"
        )
        huidig = self._item("A", "Een", "set/een.mp3")
        ander = self._item(
            "B", "Twee", "set/twee.mp3", recovery_set_id=andere_id
        )
        client = FakeSpotifyClient()
        via_id = voer_spotify_search_uit(
            self.db, recovery_set_id=self.set_id,
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual((via_id.total, via_id.processed), (1, 1))
        self.assertEqual(
            self.db.verbinding.execute(
                "SELECT spotify_status FROM recovery_items WHERE id=?",
                (ander,),
            ).fetchone()["spotify_status"],
            None,
        )
        via_naam = voer_spotify_search_uit(
            self.db, archive_set_name="andere",
            client=client, uitvoer=io.StringIO(),
        )
        self.assertEqual(via_naam.recovery_set_id, andere_id)
        self.assertEqual(via_naam.processed, 1)
        self.assertIsNotNone(huidig)

    def test_automatisch_meest_recente_geldige_set_en_lijst(self):
        andere_id = verkrijg_of_maak_recovery_set(
            self.db, "Nieuwste.part01.rar"
        )
        self.db.verbinding.execute(
            "UPDATE recovery_sets SET updated_at='2099-01-01' WHERE id=?",
            (andere_id,),
        )
        self.db.verbinding.commit()
        self._item(
            "A", "Titel", "nieuw/titel.mp3", recovery_set_id=andere_id
        )
        log = io.StringIO()
        summary = voer_spotify_search_uit(
            self.db, client=FakeSpotifyClient(), uitvoer=log
        )
        self.assertEqual(summary.recovery_set_id, andere_id)
        self.assertIn("Automatisch geselecteerde recovery-set", log.getvalue())
        sets = beschikbare_recovery_sets(self.db)
        self.assertEqual(sets[0].archive_set_name, "Nieuwste")
        self.assertEqual(sets[0].recovery_item_count, 1)

    def test_automatische_resultaten_skip_force_en_handmatig_nooit(self):
        automatisch = self._item("A", "Een", "set/auto.mp3")
        handmatig = self._item("B", "Twee", "set/manual2.mp3")
        self.db.verbinding.execute(
            "UPDATE recovery_items SET spotify_status='MATCHED' WHERE id=?",
            (automatisch,),
        )
        self.db.verbinding.execute(
            """
            INSERT INTO spotify_smart_results (
              recovery_item_id, local_path, status, checked_at, reason
            ) VALUES (?, 'set/manual2.mp3', 'REVIEWED_NONE', 'nu', 'handmatig')
            """,
            (handmatig,),
        )
        self.db.verbinding.commit()
        standaard = voer_spotify_search_uit(
            self.db, recovery_set_id=self.set_id,
            client=FakeSpotifyClient(), uitvoer=io.StringIO(),
        )
        self.assertEqual(standaard.processed, 0)
        self.assertEqual(standaard.skipped_automatic, 1)
        self.assertEqual(standaard.skipped_manual, 1)
        geforceerd = voer_spotify_search_uit(
            self.db, recovery_set_id=self.set_id, force=True,
            client=FakeSpotifyClient(), uitvoer=io.StringIO(),
        )
        self.assertEqual(geforceerd.processed, 1)
        self.assertEqual(geforceerd.skipped_manual, 1)

    def test_grote_batch_wordt_beveiligd_en_kan_expliciet(self):
        for nummer in range(501):
            self._item(
                "A", f"Track {nummer}", f"set/track{nummer}.mp3"
            )
        with self.assertRaisesRegex(
            SpotifyRecoverySetError, "Aantal recovery-items: 501"
        ):
            voer_spotify_search_uit(
                self.db, recovery_set_id=self.set_id,
                client=FakeSpotifyClient(), uitvoer=io.StringIO(),
            )
        toegestaan = voer_spotify_search_uit(
            self.db, recovery_set_id=self.set_id,
            allow_large_batch=True, client=FakeSpotifyClient(),
            uitvoer=io.StringIO(),
        )
        self.assertEqual(toegestaan.processed, 501)

    def test_gerichte_set_met_24_definitieve_items(self):
        set_id = verkrijg_of_maak_recovery_set(
            self.db, "Megaman2007.part01.rar"
        )
        for nummer in range(24):
            self._item(
                "Artist", f"Track {nummer}",
                f"2007/track{nummer}.mp3", recovery_set_id=set_id,
            )
        summary = voer_spotify_search_uit(
            self.db, archive_set_name="Megaman2007",
            client=FakeSpotifyClient(), uitvoer=io.StringIO(),
        )
        self.assertEqual(summary.total, 24)
        self.assertEqual(summary.processed, 24)


if __name__ == "__main__":
    unittest.main()
