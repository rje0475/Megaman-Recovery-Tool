"""Slimme Spotify-zoek-, versie- en kandidaatworkflow zonder mediawijzigingen."""

import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from database import DATABASE_BESTAND, SQLiteDatabase
from spotify import SpotifyApiFout, SpotifyClient, SpotifyConfiguratieFout


FOUND, AMBIGUOUS, NOT_FOUND = "FOUND", "AMBIGUOUS", "NOT_FOUND"
INSUFFICIENT_IDENTITY = "INSUFFICIENT_IDENTITY"
MANUAL, REVIEWED_NONE = "MANUAL", "REVIEWED_NONE"
MAX_KANDIDATEN = 10
FOUND_DREMPEL = 0.84
MINIMUM_DREMPEL = 0.58
MINIMUM_VERSCHIL = 0.07

VERSIES = (
    "radio edit", "radio version", "extended mix", "extended version",
    "original mix", "club mix", "dub mix", "instrumental", "acoustic",
    "unplugged", "live", "remaster", "remastered", "album version",
    "single version", "edit", "bootleg", "sped up", "slowed",
    "nightcore", "karaoke", "cover", "tribute",
)
ONGEWENST = {
    "live", "karaoke", "tribute", "cover", "instrumental", "acoustic",
    "bootleg", "sped up", "slowed", "nightcore",
}
RUIS = re.compile(
    r"(?i)\b(?:official\s+(?:music\s+)?video|official\s+audio|music\s+video|"
    r"lyric\s+video|lyrics?|hd|hq|4k)\b"
)
TRACKNUMMER = re.compile(r"^\s*\d{1,3}(?:\s*[._-]\s*|\s+)")
FEAT = re.compile(r"(?i)\b(?:feat(?:uring)?|ft|with)\.?\s+")


@dataclass(frozen=True)
class TitelInfo:
    origineel: str
    volledig: str
    basistitel: str
    versie: str | None = None
    remixer: str | None = None


@dataclass(frozen=True)
class Score:
    totaal: float
    artiest: float
    titel: float
    versie: float
    duur: float
    reden: str


@dataclass(frozen=True)
class SlimSpotifyOverzicht:
    verwerkt: int
    found: int
    ambiguous: int
    not_found: int
    insufficient_identity: int
    overgeslagen: int
    fouten: int
    credentials_ontbreken: bool = False


class SpotifyZoekFout(RuntimeError):
    pass


def normaliseer_tekst(waarde):
    tekst = unicodedata.normalize("NFKD", str(waarde or ""))
    tekst = "".join(c for c in tekst if not unicodedata.combining(c))
    tekst = FEAT.sub(" feat ", tekst)
    tekst = tekst.replace("_", " ")
    return re.sub(r"[^a-z0-9]+", " ", tekst.casefold()).strip()


def parseer_titel(titel):
    origineel = str(titel or "").strip()
    tekst = re.sub(r"(?i)\.(?:mp3|flac|m4a|wav|ogg)$", "", origineel)
    tekst = TRACKNUMMER.sub("", tekst).replace("_", " ")
    tekst = RUIS.sub(" ", tekst)
    tekst = re.sub(r"\s+", " ", tekst).strip(" -–—_")
    versie = None
    remixer = None
    basis = tekst
    delen = re.findall(r"[\(\[]([^\)\]]+)[\)\]]", tekst)
    suffix = re.search(r"\s*[-–—]\s*([^-–—]+)$", tekst)
    kandidaten = delen + ([suffix.group(1)] if suffix else [])
    for deel in kandidaten:
        laag = deel.casefold().strip()
        specifieke_remix = re.match(r"(.+?)\s+remix$", deel, re.I)
        gevonden = next((v for v in VERSIES if v in laag), None)
        if specifieke_remix:
            versie = "Remix"
            remixer = specifieke_remix.group(1).strip()
            break
        if "remix" in laag:
            versie = "Remix"
            remixer = re.sub(r"(?i)\s*remix\s*$", "", deel).strip() or None
            break
        if gevonden:
            versie = " ".join(w.capitalize() for w in gevonden.split())
            break
        if re.search(r"(?i)(?:mix|edit|version)$", deel):
            versie = deel.strip()
            break
    if versie:
        for deel in kandidaten:
            if (
                normaliseer_tekst(versie) in normaliseer_tekst(deel)
                or (remixer and normaliseer_tekst(remixer) in normaliseer_tekst(deel))
            ):
                basis = re.sub(
                    r"\s*[\(\[]" + re.escape(deel) + r"[\)\]]\s*$",
                    "", tekst, flags=re.I
                )
                basis = re.sub(
                    r"\s*[-–—]\s*" + re.escape(deel) + r"\s*$",
                    "", basis, flags=re.I
                ).strip()
                break
    # Remaster-ruis zonder betekenisvolle overige versie mag weg.
    if versie and normaliseer_tekst(versie) in ("remaster", "remastered"):
        versie = None
    volledig = re.sub(r"[\[\]]", " ", tekst)
    volledig = re.sub(r"\s+", " ", volledig).strip()
    return TitelInfo(origineel, volledig, basis or volledig, versie, remixer)


def ontleed_bestandsnaam(pad):
    naam = TRACKNUMMER.sub("", Path(str(pad)).stem).replace("_", " ")
    delen = re.split(r"\s+[-–—]\s+", naam, maxsplit=1)
    if len(delen) == 2:
        return delen[0].strip(), delen[1].strip()
    underscore = Path(str(pad)).stem.split("_", 1)
    if len(underscore) == 2:
        return underscore[0].strip(), underscore[1].strip()
    return "", naam.strip()


def zoekstrategieen(artiest, titel, pad=None):
    info = parseer_titel(titel)
    schone_artiest = re.sub(r"\s+", " ", FEAT.sub(" feat ", artiest)).strip()
    pogingen = [
        ("original_metadata", artiest.strip(), str(titel).strip()),
        ("cleaned_full", schone_artiest, info.volledig),
    ]
    if info.versie:
        pogingen.append((
            "base_and_version", schone_artiest,
            f"{info.basistitel} {info.versie}".strip(),
        ))
    pogingen.append(("base_title", schone_artiest, info.basistitel))
    if pad:
        bestand_artiest, bestand_titel = ontleed_bestandsnaam(pad)
        if bestand_titel:
            pogingen.append((
                "filename", bestand_artiest or schone_artiest,
                bestand_titel,
            ))
    pogingen.append(("title_only", "", info.basistitel))
    uniek = []
    gezien = set()
    for strategie, zoek_artiest, zoek_titel in pogingen:
        sleutel = (
            strategie, normaliseer_tekst(zoek_artiest),
            normaliseer_tekst(zoek_titel)
        )
        if zoek_titel and sleutel not in gezien:
            gezien.add(sleutel)
            uniek.append((strategie, zoek_artiest, zoek_titel))
    return uniek


def _overeenkomst(a, b):
    a, b = normaliseer_tekst(a), normaliseer_tekst(b)
    if not a or not b:
        return 0.0
    volgorde = SequenceMatcher(None, a, b).ratio()
    at, bt = set(a.split()), set(b.split())
    woorden = len(at & bt) / len(at | bt)
    return (volgorde + woorden) / 2


def score_kandidaat(
    lokaal_artiest, lokaal_titel, gevonden_artiest, gevonden_titel,
    lokaal_duur=None, gevonden_duur=None, strategie="original_metadata",
):
    lokaal, gevonden = parseer_titel(lokaal_titel), parseer_titel(gevonden_titel)
    artiest = _overeenkomst(lokaal_artiest, gevonden_artiest)
    basis = _overeenkomst(lokaal.basistitel, gevonden.basistitel)
    volledig = _overeenkomst(lokaal.volledig, gevonden.volledig)
    if lokaal.versie:
        versie = (
            1.0 if normaliseer_tekst(lokaal.versie)
            == normaliseer_tekst(gevonden.versie) else 0.15
        )
        if lokaal.remixer:
            versie = (
                1.0 if _overeenkomst(lokaal.remixer, gevonden.remixer) >= 0.85
                else 0.0
            )
    else:
        versie = 1.0 if not gevonden.versie else (
            0.1 if normaliseer_tekst(gevonden.versie) in ONGEWENST else 0.35
        )
    duur = 0.5
    if lokaal_duur and gevonden_duur:
        duur = max(0.0, 1.0 - abs(lokaal_duur - gevonden_duur) / 30000)
    totaal = (
        0.28 * artiest + 0.30 * basis + 0.14 * volledig
        + 0.23 * versie + 0.05 * duur
    )
    if strategie == "original_metadata":
        totaal += 0.02
    redenen = [
        f"artiest {artiest:.2f}", f"titel {basis:.2f}",
        f"versie {versie:.2f}", f"duur {duur:.2f}",
    ]
    return Score(
        round(max(0.0, min(totaal, 1.0)), 4),
        round(artiest, 4), round((basis + volledig) / 2, 4),
        round(versie, 4), round(duur, 4), ", ".join(redenen),
    )


def _bewaar_kandidaten(database, item_id, kandidaten):
    database.verbinding.execute(
        "UPDATE spotify_candidates SET selected = 0 WHERE recovery_item_id = ?",
        (item_id,),
    )
    for rang, kandidaat in enumerate(kandidaten[:MAX_KANDIDATEN], 1):
        database.verbinding.execute(
            """
            INSERT INTO spotify_candidates (
              recovery_item_id, spotify_track_id, spotify_url, artist, title,
              base_title, version, remixer, album, duration_ms, total_score,
              artist_score, title_score, version_score, duration_score,
              rank_number, search_strategy, search_query, selected, rejected,
              score_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      0, COALESCE((SELECT rejected FROM spotify_candidates
                         WHERE recovery_item_id=? AND spotify_track_id=?), 0), ?)
            ON CONFLICT (recovery_item_id, spotify_track_id) DO UPDATE SET
              spotify_url=excluded.spotify_url, artist=excluded.artist,
              title=excluded.title, base_title=excluded.base_title,
              version=excluded.version, remixer=excluded.remixer,
              album=excluded.album, duration_ms=excluded.duration_ms,
              total_score=excluded.total_score,
              artist_score=excluded.artist_score,
              title_score=excluded.title_score,
              version_score=excluded.version_score,
              duration_score=excluded.duration_score,
              rank_number=excluded.rank_number,
              search_strategy=excluded.search_strategy,
              search_query=excluded.search_query,
              score_reason=excluded.score_reason
            """,
            (
                item_id, kandidaat["track_id"], kandidaat["url"],
                kandidaat["artist"], kandidaat["title"],
                kandidaat["info"].basistitel, kandidaat["info"].versie,
                kandidaat["info"].remixer, kandidaat["album"],
                kandidaat["duration_ms"], kandidaat["score"].totaal,
                kandidaat["score"].artiest, kandidaat["score"].titel,
                kandidaat["score"].versie, kandidaat["score"].duur, rang,
                kandidaat["strategy"], kandidaat["query"], item_id,
                kandidaat["track_id"], kandidaat["score"].reden,
            ),
        )
    database.verbinding.commit()


def _bewaar_resultaat(database, item, lokaal, status, reden, beste=None):
    beste = beste or {}
    gevonden = beste.get("info")
    database.verbinding.execute(
        """
        INSERT INTO spotify_smart_results (
          recovery_item_id, local_path, original_artist, original_title,
          cleaned_artist, cleaned_full_title, base_title, local_version,
          local_remixer, search_strategy, search_query, spotify_track_id,
          spotify_url, found_artist, found_title, found_base_title,
          found_version, found_remixer, album, duration_ms, match_score,
          status, checked_at, reason, manually_reviewed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, 0)
        ON CONFLICT (recovery_item_id) DO UPDATE SET
          local_path=excluded.local_path,
          original_artist=excluded.original_artist,
          original_title=excluded.original_title,
          cleaned_artist=excluded.cleaned_artist,
          cleaned_full_title=excluded.cleaned_full_title,
          base_title=excluded.base_title, local_version=excluded.local_version,
          local_remixer=excluded.local_remixer,
          search_strategy=excluded.search_strategy,
          search_query=excluded.search_query,
          spotify_track_id=excluded.spotify_track_id,
          spotify_url=excluded.spotify_url, found_artist=excluded.found_artist,
          found_title=excluded.found_title,
          found_base_title=excluded.found_base_title,
          found_version=excluded.found_version,
          found_remixer=excluded.found_remixer, album=excluded.album,
          duration_ms=excluded.duration_ms, match_score=excluded.match_score,
          status=excluded.status, checked_at=excluded.checked_at,
          reason=excluded.reason
        """,
        (
            item["id"], item["verwacht_rel_pad"],
            item["bepaalde_artiest"], item["bepaalde_titel"],
            re.sub(r"\s+", " ", FEAT.sub(" feat ", item["bepaalde_artiest"] or "")).strip(),
            lokaal.volledig, lokaal.basistitel, lokaal.versie, lokaal.remixer,
            beste.get("strategy"), beste.get("query"),
            beste.get("track_id"), beste.get("url"), beste.get("artist"),
            beste.get("title"), gevonden.basistitel if gevonden else None,
            gevonden.versie if gevonden else None,
            gevonden.remixer if gevonden else None, beste.get("album"),
            beste.get("duration_ms"),
            beste.get("score").totaal if beste.get("score") else None,
            status, datetime.now().isoformat(timespec="seconds"), reden,
        ),
    )
    database.verbinding.commit()


def _zoek_kandidaten(client, item):
    uniek = {}
    strategieen = zoekstrategieen(
        item["bepaalde_artiest"] or "",
        item["bepaalde_titel"] or "",
        item["verwacht_rel_pad"],
    )
    for strategie, artiest, titel in strategieen:
        resultaten = None
        for poging in range(3):
            try:
                resultaten = client.zoek_nummers(
                    artiest, titel, limiet=10
                )
                break
            except SpotifyApiFout:
                if poging < 2:
                    time.sleep(0.25 * (2 ** poging))
            except ValueError:
                break
        if resultaten is None:
            continue
        for resultaat in resultaten:
            if not resultaat.track_id:
                continue
            score = score_kandidaat(
                item["bepaalde_artiest"] or "",
                item["bepaalde_titel"] or "",
                resultaat.artiest or "", resultaat.titel or "",
                None, resultaat.duur_ms, strategie,
            )
            kandidaat = {
                "track_id": resultaat.track_id, "url": resultaat.url,
                "artist": resultaat.artiest, "title": resultaat.titel,
                "info": parseer_titel(resultaat.titel),
                "album": resultaat.album, "duration_ms": resultaat.duur_ms,
                "score": score, "strategy": strategie,
                "query": f"{artiest} {titel}".strip(),
            }
            bestaand = uniek.get(resultaat.track_id)
            if bestaand is None or score.totaal > bestaand["score"].totaal:
                uniek[resultaat.track_id] = kandidaat
    return sorted(
        uniek.values(), key=lambda k: (-k["score"].totaal, k["track_id"])
    )[:MAX_KANDIDATEN]


def _bepaal_status(lokaal, kandidaten):
    if not kandidaten or kandidaten[0]["score"].totaal < MINIMUM_DREMPEL:
        return NOT_FOUND, "Geen kandidaat boven de minimumdrempel."
    beste = kandidaten[0]
    tweede = kandidaten[1]["score"].totaal if len(kandidaten) > 1 else 0.0
    versie_past = (
        beste["score"].versie >= 0.85 if lokaal.versie else True
    )
    if (
        beste["score"].totaal >= FOUND_DREMPEL
        and beste["score"].totaal - tweede >= MINIMUM_VERSCHIL
        and versie_past
    ):
        return FOUND, "Eén overtuigende kandidaat met passende versie."
    return AMBIGUOUS, "Meerdere kandidaten of versies liggen te dicht bij elkaar."


def voer_spotify_smart_uit(
    map_pad, retry=False, database_pad=DATABASE_BESTAND,
    client=None, uitvoer=None,
):
    uitvoer = uitvoer or sys.stdout
    map_pad = Path(map_pad)
    if not map_pad.is_dir():
        raise SpotifyZoekFout(f"Spotify-map bestaat niet: {map_pad}")
    if not Path(database_pad).is_file():
        raise SpotifyZoekFout(f"Database bestaat niet: {Path(database_pad).resolve()}")
    if client is None:
        try:
            client = SpotifyClient.uit_omgeving()
        except SpotifyConfiguratieFout as fout:
            uitvoer.write("Spotify-credentials ontbreken.\n")
            return SlimSpotifyOverzicht(0, 0, 0, 0, 0, 0, 0, True)
    database = SQLiteDatabase(database_pad)
    telling = dict(
        verwerkt=0, found=0, ambiguous=0, not_found=0,
        insufficient_identity=0, overgeslagen=0, fouten=0,
    )
    try:
        items = [
            dict(r) for r in database.verbinding.execute(
                """
                SELECT items.*, resultaten.status bestaand_status
                FROM recovery_items items
                LEFT JOIN spotify_smart_results resultaten
                  ON resultaten.recovery_item_id = items.id
                ORDER BY items.id
                """
            )
        ]
        for item in items:
            bestaand = item["bestaand_status"]
            toegestaan = (
                bestaand in (NOT_FOUND, AMBIGUOUS) if retry
                else bestaand is None
            )
            if not toegestaan:
                telling["overgeslagen"] += 1
                continue
            artiest, titel = item["bepaalde_artiest"], item["bepaalde_titel"]
            lokaal = parseer_titel(titel)
            if (
                not titel or not lokaal.basistitel
                or (not artiest and len(lokaal.basistitel.split()) < 2)
                or (item["identiteit_betrouwbaarheid"] or 0) < 0.60
            ):
                _bewaar_resultaat(
                    database, item, lokaal, INSUFFICIENT_IDENTITY,
                    "Onvoldoende bruikbare artiest of titel.",
                )
                telling["insufficient_identity"] += 1
                telling["verwerkt"] += 1
                continue
            uitvoer.write(f"Spotify zoeken: {item['verwacht_rel_pad']}\n")
            try:
                kandidaten = _zoek_kandidaten(client, item)
                _bewaar_kandidaten(database, item["id"], kandidaten)
                status, reden = _bepaal_status(lokaal, kandidaten)
                beste = kandidaten[0] if kandidaten else None
                if beste:
                    afgewezen = database.verbinding.execute(
                        """
                        SELECT rejected FROM spotify_candidates
                        WHERE recovery_item_id=? AND spotify_track_id=?
                        """, (item["id"], beste["track_id"])
                    ).fetchone()
                    if afgewezen and afgewezen["rejected"]:
                        status, reden = AMBIGUOUS, "Beste kandidaat was eerder afgewezen."
                _bewaar_resultaat(database, item, lokaal, status, reden, beste)
                if status == FOUND and beste:
                    database.verbinding.execute(
                        """
                        UPDATE spotify_candidates
                        SET selected = CASE WHEN spotify_track_id=? THEN 1 ELSE 0 END
                        WHERE recovery_item_id=?
                        """, (beste["track_id"], item["id"])
                    )
                    database.verbinding.commit()
                telling[status.casefold()] += 1
            except Exception as fout:
                telling["fouten"] += 1
                uitvoer.write(f"Spotify-fout: {fout}\n")
            telling["verwerkt"] += 1
    finally:
        database.sluit()
    return SlimSpotifyOverzicht(**telling)


def kies_kandidaat(database, recovery_item_id, kandidaat_id):
    kandidaat = database.verbinding.execute(
        """
        SELECT * FROM spotify_candidates
        WHERE id=? AND recovery_item_id=?
        """, (kandidaat_id, recovery_item_id)
    ).fetchone()
    if kandidaat is None:
        raise SpotifyZoekFout("Spotify-kandidaat bestaat niet.")
    database.verbinding.execute(
        "UPDATE spotify_candidates SET selected=0 WHERE recovery_item_id=?",
        (recovery_item_id,),
    )
    database.verbinding.execute(
        "UPDATE spotify_candidates SET selected=1, rejected=0 WHERE id=?",
        (kandidaat_id,),
    )
    database.verbinding.execute(
        """
        UPDATE spotify_smart_results SET
          spotify_track_id=?, spotify_url=?, found_artist=?, found_title=?,
          found_base_title=?, found_version=?, found_remixer=?, album=?,
          duration_ms=?, match_score=?, status='MANUAL',
          checked_at=?, reason='Handmatig gekozen kandidaat.',
          manually_reviewed=1
        WHERE recovery_item_id=?
        """,
        (
            kandidaat["spotify_track_id"], kandidaat["spotify_url"],
            kandidaat["artist"], kandidaat["title"], kandidaat["base_title"],
            kandidaat["version"], kandidaat["remixer"], kandidaat["album"],
            kandidaat["duration_ms"], kandidaat["total_score"],
            datetime.now().isoformat(timespec="seconds"), recovery_item_id,
        ),
    )
    database.verbinding.commit()


def markeer_geen_kandidaat(database, recovery_item_id):
    database.verbinding.execute(
        """
        UPDATE spotify_candidates SET selected=0, rejected=1
        WHERE recovery_item_id=?
        """, (recovery_item_id,)
    )
    database.verbinding.execute(
        """
        UPDATE spotify_smart_results SET status='REVIEWED_NONE',
          spotify_track_id=NULL, spotify_url=NULL, manually_reviewed=1,
          checked_at=?, reason='Gebruiker koos geen van deze.'
        WHERE recovery_item_id=?
        """, (datetime.now().isoformat(timespec="seconds"), recovery_item_id)
    )
    database.verbinding.commit()
