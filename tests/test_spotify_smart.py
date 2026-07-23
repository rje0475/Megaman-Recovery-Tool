import io
import tempfile
import unittest
from pathlib import Path

from database import maak_database
from spotify import MuziekResultaat
from spotify_smart import (
    AMBIGUOUS,
    FOUND,
    MANUAL,
    REVIEWED_NONE,
    kies_kandidaat,
    markeer_geen_kandidaat,
    normaliseer_tekst,
    ontleed_bestandsnaam,
    parseer_titel,
    score_kandidaat,
    zoekstrategieen,
    voer_spotify_smart_uit,
)


def track(track_id, titel, artiest="Artist", duur=200000):
    return MuziekResultaat(
        provider="spotify", zoek_artiest=artiest, zoek_titel=titel,
        gevonden=True, track_id=track_id,
        url=f"https://open.spotify.com/track/{track_id}",
        artiest=artiest, titel=titel, album="Album", duur_ms=duur,
    )


class ParserTest(unittest.TestCase):
    def test_opschonen_en_tracknummer(self):
        info = parseer_titel(
            "01 - Mijn_ Track [Official Video] (Remastered 2011).mp3"
        )
        self.assertEqual(normaliseer_tekst(info.basistitel), "mijn track")

    def test_versies_blijven_apart_bewaard(self):
        extended = parseer_titel("No Limit (Extended Mix)")
        radio = parseer_titel("No Limit [Radio Edit]")
        remix = parseer_titel("Tracknaam (Tiësto Remix)")
        self.assertEqual((extended.basistitel, extended.versie),
                         ("No Limit", "Extended Mix"))
        self.assertEqual(radio.versie, "Radio Edit")
        self.assertEqual(remix.basistitel, "Tracknaam")
        self.assertEqual(remix.versie, "Remix")
        self.assertEqual(remix.remixer, "Tiësto")

    def test_featuring_underscores_en_bestandsnaam(self):
        self.assertEqual(
            normaliseer_tekst("A ft. B__Titel"), "a feat b titel"
        )
        self.assertEqual(
            ontleed_bestandsnaam("03 - Artist - Titel.mp3"),
            ("Artist", "Titel"),
        )

    def test_strategievolgorde_en_late_basisfallback(self):
        strategieen = zoekstrategieen(
            "Artist", "No Limit (Extended Mix)", "Artist - No Limit.mp3"
        )
        namen = [s[0] for s in strategieen]
        self.assertEqual(namen[:3], [
            "original_metadata", "cleaned_full", "base_and_version"
        ])
        self.assertLess(namen.index("base_title"), namen.index("title_only"))


class ScoreTest(unittest.TestCase):
    def test_extended_radio_en_standaardversie(self):
        extended = score_kandidaat(
            "Artist", "No Limit (Extended Mix)",
            "Artist", "No Limit - Extended Mix",
        )
        gewoon = score_kandidaat(
            "Artist", "No Limit (Extended Mix)", "Artist", "No Limit"
        )
        radio = score_kandidaat(
            "Artist", "No Limit (Extended Mix)",
            "Artist", "No Limit - Radio Edit",
        )
        self.assertGreater(extended.totaal, gewoon.totaal)
        self.assertGreater(gewoon.totaal, radio.totaal)

    def test_specifieke_remixer_wint(self):
        juist = score_kandidaat(
            "Artist", "Track (Tiësto Remix)",
            "Artist", "Track - Tiësto Remix",
        )
        fout = score_kandidaat(
            "Artist", "Track (Tiësto Remix)",
            "Artist", "Track - Armin van Buuren Remix",
        )
        algemeen = score_kandidaat(
            "Artist", "Track (Tiësto Remix)", "Artist", "Track - Remix"
        )
        self.assertGreater(juist.totaal, fout.totaal)
        self.assertGreater(juist.totaal, algemeen.totaal)

    def test_standaard_straft_live_karaoke_cover_en_remix(self):
        gewoon = score_kandidaat("Artist", "Track", "Artist", "Track")
        for versie in ("Live", "Karaoke", "Cover", "Remix"):
            with self.subTest(versie=versie):
                afwijkend = score_kandidaat(
                    "Artist", "Track", "Artist", f"Track - {versie}"
                )
                self.assertGreater(gewoon.totaal, afwijkend.totaal)

    def test_duur_en_verkeerde_artiest_beinvloeden_score(self):
        passend = score_kandidaat(
            "Artist", "Track", "Artist", "Track", 200000, 201000
        )
        afwijkend = score_kandidaat(
            "Artist", "Track", "Ander", "Track", 200000, 300000
        )
        self.assertGreater(passend.totaal, afwijkend.totaal)


class FakeClient:
    def __init__(self, antwoorden):
        self.antwoorden = antwoorden
        self.aanroepen = []

    def zoek_nummers(self, artiest, titel, limiet=10):
        self.aanroepen.append((artiest, titel))
        return self.antwoorden.get(titel, self.antwoorden.get("*", []))


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = maak_database(self.root / "test.sqlite3")
        cursor = self.db.verbinding.execute(
            """
            INSERT INTO recovery_items (
              rar_set_key, verwacht_rel_pad, verwacht_rel_pad_norm,
              probleem_type, probleem_bron, feit_ontbreekt,
              spotify_verwerkt, download_verwerkt, geplaatst,
              bepaalde_artiest, bepaalde_titel, identiteit_bron,
              identiteit_betrouwbaarheid, aangemaakt_op, bijgewerkt_op
            ) VALUES (
              'set', 'Artist - Track.mp3', 'artist - track.mp3',
              'ontbreekt', 'test', 1, 0, 0, 0,
              'Artist', 'Track', 'test', .9, 'nu', 'nu'
            )
            """
        )
        self.item_id = cursor.lastrowid
        self.db.verbinding.commit()

    def tearDown(self):
        self.db.sluit()
        self.temp.cleanup()

    def _zoek(self, client, retry=False):
        return voer_spotify_smart_uit(
            self.root, retry=retry, database_pad=self.root / "test.sqlite3",
            client=client, uitvoer=io.StringIO(),
        )

    def test_ontdubbelt_en_bewaart_maximaal_tien(self):
        tracks = [track(f"id-{i}", f"Track {i}") for i in range(12)]
        tracks += [track("id-0", "Track")]
        self._zoek(FakeClient({"*": tracks}))
        aantal = self.db.verbinding.execute(
            "SELECT COUNT(*) aantal FROM spotify_candidates"
        ).fetchone()["aantal"]
        self.assertEqual(aantal, 10)

    def test_found_wordt_opgeslagen_en_niet_opnieuw_gezocht(self):
        client = FakeClient({"*": [track("goed", "Track")]})
        eerste = self._zoek(client)
        self.assertEqual(eerste.found, 1)
        tweede_client = FakeClient({"*": []})
        tweede = self._zoek(tweede_client)
        self.assertEqual(tweede.overgeslagen, 1)
        self.assertEqual(tweede_client.aanroepen, [])

    def test_ambiguous_wordt_alleen_via_retry_opnieuw_gezocht(self):
        self._zoek(FakeClient({"*": [
            track("a", "Track"), track("b", "Track")
        ]}))
        normaal = FakeClient({"*": [track("c", "Track")]})
        self._zoek(normaal)
        self.assertEqual(normaal.aanroepen, [])
        retry = FakeClient({"*": [track("c", "Track")]})
        self._zoek(retry, retry=True)
        self.assertTrue(retry.aanroepen)

    def test_bijna_gelijke_kandidaten_worden_ambiguous(self):
        self._zoek(FakeClient({"*": [
            track("a", "Track"), track("b", "Track")
        ]}))
        status = self.db.verbinding.execute(
            "SELECT status FROM spotify_smart_results"
        ).fetchone()["status"]
        self.assertEqual(status, AMBIGUOUS)

    def test_manual_en_reviewed_none_worden_niet_overschreven(self):
        self._zoek(FakeClient({"*": [track("goed", "Track")]}))
        kandidaat = self.db.verbinding.execute(
            "SELECT id FROM spotify_candidates LIMIT 1"
        ).fetchone()["id"]
        kies_kandidaat(self.db, self.item_id, kandidaat)
        self._zoek(FakeClient({"*": []}), retry=True)
        status = self.db.verbinding.execute(
            "SELECT status FROM spotify_smart_results"
        ).fetchone()["status"]
        self.assertEqual(status, MANUAL)
        markeer_geen_kandidaat(self.db, self.item_id)
        self._zoek(FakeClient({"*": [track("nieuw", "Track")]}), retry=True)
        status = self.db.verbinding.execute(
            "SELECT status FROM spotify_smart_results"
        ).fetchone()["status"]
        self.assertEqual(status, REVIEWED_NONE)

    def test_slechts_een_handmatige_kandidaat_geselecteerd(self):
        self._zoek(FakeClient({"*": [
            track("a", "Track"), track("b", "Track - Live")
        ]}))
        ids = [
            r["id"] for r in self.db.verbinding.execute(
                "SELECT id FROM spotify_candidates ORDER BY id"
            )
        ]
        kies_kandidaat(self.db, self.item_id, ids[-1])
        aantal = self.db.verbinding.execute(
            "SELECT SUM(selected) aantal FROM spotify_candidates"
        ).fetchone()["aantal"]
        self.assertEqual(aantal, 1)


if __name__ == "__main__":
    unittest.main()
