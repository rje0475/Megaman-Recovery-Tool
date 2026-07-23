import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from database import SQLiteDatabase, maak_database
from spotify import MuziekResultaat, SpotifyApiFout
from spotify_recovery import (
    _verwachte_duur_ms,
    bereken_matchscore,
    exporteer_spotify_recovery_playlist,
    voer_spotify_recovery_uit,
)


def kandidaat(artiest, titel, track_id="track-1", album=None, duur_ms=None):
    return MuziekResultaat(
        provider="spotify",
        zoek_artiest=artiest,
        zoek_titel=titel,
        gevonden=True,
        track_id=track_id,
        url=f"https://open.spotify.com/track/{track_id}",
        artiest=artiest,
        titel=titel,
        album=album,
        duur_ms=duur_ms,
    )


class FakeClient:
    def __init__(self, antwoorden):
        self.antwoorden = antwoorden
        self.aanroepen = []

    def zoek_nummers(self, artiest, titel, limiet=10):
        self.aanroepen.append((artiest, titel))
        antwoord = self.antwoorden.get(artiest, self.antwoorden.get("*", []))
        if isinstance(antwoord, Exception):
            raise antwoord
        return antwoord


class SpotifyRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = maak_database(self.root / "test.sqlite3")
        self.export = self.root / "playlist.json"

    def tearDown(self):
        self.database.sluit()
        self.temp.cleanup()

    def _item(
        self, artiest="Artiest", titel="Titel", album=None,
        betrouwbaarheid=0.9, handtekening="identiteit-1",
        pad=r"Album\01 - Artiest - Titel.mp3"
    ):
        cursor = self.database.verbinding.execute(
            """
            INSERT INTO recovery_items (
                rar_set_key, verwacht_rel_pad, verwacht_rel_pad_norm,
                probleem_type, probleem_bron, feit_ontbreekt,
                spotify_verwerkt, download_verwerkt, geplaatst,
                bepaalde_artiest, bepaalde_titel, bepaald_album,
                identiteit_bron, identiteit_betrouwbaarheid,
                identiteit_bron_handtekening, aangemaakt_op, bijgewerkt_op
            ) VALUES (
                'set-a', ?, ?, 'ontbreekt', 'rar_inventory', 1,
                0, 0, 0, ?, ?, ?, 'test', ?, ?, ?, ?
            )
            """,
            (
                pad, pad.casefold(), artiest, titel, album,
                betrouwbaarheid, handtekening,
                "2026-01-01T00:00:00", "2026-01-01T00:00:00",
            ),
        )
        self.database.verbinding.commit()
        return cursor.lastrowid

    def _run(self, client, nu=None):
        return voer_spotify_recovery_uit(
            self.database,
            client=client,
            uitvoer=io.StringIO(),
            export_pad=self.export,
            slaapfunctie=lambda seconden: None,
            nu=nu or datetime(2026, 1, 10, 12, 0, 0),
        )

    def _resultaat(self):
        return self.database.verbinding.execute(
            "SELECT * FROM recovery_provider_resultaten"
        ).fetchone()

    def _mp3_item(self, bestand):
        cursor = self.database.verbinding.execute(
            """
            INSERT INTO mp3_bestanden (
                relatief_pad, bestand, bestaat, nul_bytes, rar_status,
                ffmpeg_status
            ) VALUES (?, ?, 1, 0, 'NIET_GECONTROLEERD',
                      'NIET_GECONTROLEERD')
            """,
            (Path(bestand).name, str(bestand)),
        )
        self.database.verbinding.execute(
            "UPDATE mp3_bestanden SET id = rowid WHERE rowid = ?",
            (cursor.lastrowid,),
        )
        self.database.verbinding.commit()
        return {"mp3_id": cursor.lastrowid}

    def test_verwachte_duur_ontbrekend_of_leeg_bestand_is_none(self):
        ontbrekend = self.root / "ontbreekt.mp3"
        leeg = self.root / "leeg.mp3"
        leeg.write_bytes(b"")

        self.assertIsNone(
            _verwachte_duur_ms(
                self.database, self._mp3_item(ontbrekend)
            )
        )
        self.assertIsNone(
            _verwachte_duur_ms(self.database, self._mp3_item(leeg))
        )

    def test_verwachte_duur_beschadigde_mp3_is_none(self):
        class HeaderNotFoundError(Exception):
            pass

        beschadigd = self.root / "beschadigd.mp3"
        beschadigd.write_bytes(b"dit is geen MPEG-frame")

        with patch.dict(sys.modules, {
            "mutagen": SimpleNamespace(
                File=lambda bestand: (_ for _ in ()).throw(
                    HeaderNotFoundError("can't sync to MPEG frame")
                )
            )
        }):
            self.assertIsNone(
                _verwachte_duur_ms(
                    self.database, self._mp3_item(beschadigd)
                )
            )

    def test_verwachte_duur_mutagen_leesfout_is_none(self):
        bestand = self.root / "onleesbaar.mp3"
        bestand.write_bytes(b"niet leeg")

        with patch.dict(sys.modules, {
            "mutagen": SimpleNamespace(
                File=lambda bestand: (_ for _ in ()).throw(
                    RuntimeError("kan niet lezen")
                )
            )
        }):
            self.assertIsNone(
                _verwachte_duur_ms(
                    self.database, self._mp3_item(bestand)
                )
            )

    def test_sterke_artiest_titelmatch(self):
        self._item()
        resultaat = self._run(
            FakeClient({"Artiest": [kandidaat("Artiest", "Titel")]})
        )
        self.assertEqual(resultaat.gevonden, 1)
        self.assertEqual(self._resultaat()["resultaat_type"], "found")
        self.assertEqual(self._resultaat()["matchscore"], 1.0)

    def test_verkeerde_artiest_is_geen_match(self):
        score = bereken_matchscore(
            "Goede Artiest", "Titel", "Andere Artiest", "Titel"
        )
        self.assertLess(score, 0.82)

    def test_afwijkende_titelvariant_kan_matchen(self):
        score = bereken_matchscore(
            "Artiest", "Mijn Titel",
            "Artiest", "Mijn Titel - Remastered 2011"
        )
        self.assertGreaterEqual(score, 0.82)

    def test_ambiguous_resultaat(self):
        self._item()
        self._run(FakeClient({
            "Artiest": [kandidaat("Artiest", "Titel Live")]
        }))
        self.assertEqual(self._resultaat()["resultaat_type"], "ambiguous")
        self.assertFalse(json.loads(self.export.read_text()))

    def test_not_found(self):
        self._item()
        resultaat = self._run(FakeClient({"Artiest": []}))
        self.assertEqual(resultaat.niet_gevonden, 1)
        self.assertEqual(self._resultaat()["resultaat_type"], "not_found")

    def test_api_fout_stopt_volgend_item_niet(self):
        self._item(artiest="Fout", pad="fout.mp3")
        self._item(
            artiest="Goed", pad="goed.mp3", handtekening="identiteit-2"
        )
        client = FakeClient({
            "Fout": SpotifyApiFout("tijdelijk"),
            "Goed": [kandidaat("Goed", "Titel", "track-goed")],
        })
        resultaat = self._run(client)
        self.assertEqual(resultaat.fouten, 1)
        self.assertEqual(resultaat.gevonden, 1)

    def test_found_wordt_hervat_en_overgeslagen(self):
        self._item()
        eerste = FakeClient({
            "Artiest": [kandidaat("Artiest", "Titel")]
        })
        self._run(eerste)
        tweede = FakeClient({"Artiest": []})
        resultaat = self._run(tweede)
        self.assertEqual(resultaat.overgeslagen, 1)
        self.assertEqual(tweede.aanroepen, [])

    def test_retrytermijn_voor_not_found_en_error(self):
        self._item()
        for soort in ("not_found", "error"):
            with self.subTest(soort=soort):
                self.database.verbinding.execute(
                    "DELETE FROM recovery_provider_resultaten"
                )
                now = datetime(2026, 1, 10, 12, 0, 0)
                self._run(FakeClient({"Artiest": []}), now)
                self.database.verbinding.execute(
                    """
                    UPDATE recovery_provider_resultaten
                    SET resultaat_type = ?, retry_na = ?
                    """,
                    (
                        soort,
                        (now + timedelta(days=1)).isoformat(),
                    ),
                )
                self.database.verbinding.commit()
                client = FakeClient({"Artiest": []})
                resultaat = self._run(client, now)
                self.assertEqual(resultaat.overgeslagen, 1)
                self.assertEqual(client.aanroepen, [])

    def test_verbeterde_identiteit_mag_opnieuw_zoeken(self):
        item_id = self._item()
        self._run(FakeClient({"Artiest": []}))
        self.database.verbinding.execute(
            """
            UPDATE recovery_items
            SET bepaalde_titel = 'Betere Titel'
            WHERE id = ?
            """,
            (item_id,),
        )
        self.database.verbinding.commit()
        client = FakeClient({
            "Artiest": [kandidaat("Artiest", "Betere Titel")]
        })
        resultaat = self._run(client)
        self.assertEqual(resultaat.gevonden, 1)
        self.assertTrue(client.aanroepen)

    def test_playlist_export_bevat_alleen_found(self):
        found_id = self._item(pad="found.mp3")
        ambiguous_id = self._item(
            pad="ambiguous.mp3", handtekening="identiteit-2"
        )
        for item_id, soort, track in (
            (found_id, "found", "track-found"),
            (ambiguous_id, "ambiguous", "track-ambiguous"),
        ):
            self.database.verbinding.execute(
                """
                INSERT INTO recovery_provider_resultaten (
                    recovery_item_id, provider, provider_track_id,
                    provider_url, gevonden_artiest, gevonden_titel,
                    gevonden_album, zoekmethode, matchscore,
                    resultaat_type, gezocht_op, identiteit_handtekening
                ) VALUES (?, 'spotify', ?, 'url', 'A', 'T', 'Album',
                          'original', 0.9, ?, 'nu', 'sig')
                """,
                (item_id, track, soort),
            )
        self.database.verbinding.commit()
        aantal = exporteer_spotify_recovery_playlist(
            self.database, self.export
        )
        data = json.loads(self.export.read_text(encoding="utf-8"))
        self.assertEqual(aantal, 1)
        self.assertEqual(data[0]["spotify_track_id"], "track-found")

    def test_playlist_export_is_atomisch(self):
        self.export.write_text("bestaand", encoding="utf-8")
        with patch("spotify_recovery.os.replace", side_effect=OSError):
            with self.assertRaises(OSError):
                exporteer_spotify_recovery_playlist(
                    self.database, self.export
                )
        self.assertEqual(self.export.read_text(), "bestaand")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_ontbrekende_credentials_slaan_veilig_over(self):
        self._item()
        with patch.dict(os.environ, {}, clear=True):
            resultaat = voer_spotify_recovery_uit(
                self.database,
                uitvoer=io.StringIO(),
                export_pad=self.export,
            )
        self.assertTrue(resultaat.credentials_ontbreken)
        self.assertTrue(self.export.exists())

    def test_onvoldoende_identiteit_wordt_apart_geteld(self):
        self._item(betrouwbaarheid=0.59)
        resultaat = self._run(FakeClient({"Artiest": []}))
        self.assertEqual(resultaat.onvoldoende_identiteit, 1)
        self.assertEqual(resultaat.geschikt, 0)


class SpotifyRecoveryMigratieTest(unittest.TestCase):
    def test_bestaande_database_krijgt_recovery_provider_tabel(self):
        with tempfile.TemporaryDirectory() as tijdelijke_map:
            pad = Path(tijdelijke_map) / "bestaand.sqlite3"
            verbinding = sqlite3.connect(pad)
            verbinding.execute(
                "CREATE TABLE bestaand (id INTEGER PRIMARY KEY)"
            )
            verbinding.commit()
            verbinding.close()
            database = SQLiteDatabase(pad)
            kolommen = {
                rij["name"] for rij in database.verbinding.execute(
                    "PRAGMA table_info(recovery_provider_resultaten)"
                )
            }
            database.sluit()
        self.assertTrue({
            "recovery_item_id", "provider", "provider_track_id",
            "provider_url", "gevonden_artiest", "gevonden_titel",
            "gevonden_album", "gevonden_duur_ms", "zoekmethode",
            "matchscore", "resultaat_type", "foutmelding", "gezocht_op",
        }.issubset(kolommen))


if __name__ == "__main__":
    unittest.main()
