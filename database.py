import sqlite3
from datetime import datetime
from pathlib import Path

from paden import normaliseer_relatief_pad_sleutel


DATABASE_BESTAND = Path("megaman_recovery.db")


class SQLiteDatabase:
    """
    Bied dezelfde eenvoudige interface als de eerdere in-memory database.
    """

    def __init__(self, pad=DATABASE_BESTAND):
        self.pad = Path(pad)
        self.verbinding = sqlite3.connect(self.pad)
        self.verbinding.row_factory = sqlite3.Row
        self.verbinding.execute("PRAGMA foreign_keys = ON")
        self._maak_tabel()

    def _maak_tabel(self):
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS mp3_bestanden (
                id INTEGER,
                relatief_pad TEXT PRIMARY KEY,
                bestand TEXT NOT NULL,
                bestaat INTEGER NOT NULL,
                nul_bytes INTEGER NOT NULL,
                rar_status TEXT NOT NULL,
                rar_type TEXT,
                ffmpeg_status TEXT NOT NULL,
                ffmpeg_type TEXT,
                ffmpeg_melding TEXT
            )
            """
        )
        mp3_kolommen = {
            rij["name"]
            for rij in self.verbinding.execute(
                "PRAGMA table_info(mp3_bestanden)"
            )
        }

        if "id" not in mp3_kolommen:
            self.verbinding.execute(
                "ALTER TABLE mp3_bestanden ADD COLUMN id INTEGER"
            )

        self.verbinding.execute(
            """
            UPDATE mp3_bestanden
            SET id = rowid
            WHERE id IS NULL
            """
        )
        self.verbinding.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_mp3_bestanden_id
            ON mp3_bestanden (id)
            """
        )
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_resultaten (
                relatief_pad TEXT NOT NULL,
                provider TEXT NOT NULL,
                zoek_artiest TEXT NOT NULL,
                zoek_titel TEXT NOT NULL,
                gevonden INTEGER NOT NULL,
                track_id TEXT,
                url TEXT,
                artiest TEXT,
                titel TEXT,
                album TEXT,
                duur_ms INTEGER,
                zoekmethode TEXT NOT NULL DEFAULT 'not_found',
                PRIMARY KEY (relatief_pad, provider),
                FOREIGN KEY (relatief_pad)
                    REFERENCES mp3_bestanden (relatief_pad)
                    ON DELETE CASCADE
            )
            """
        )
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS rar_sets (
                rar_set_key TEXT PRIMARY KEY,
                rar_startbestand TEXT NOT NULL,
                listing_volledig INTEGER NOT NULL,
                listing_fout TEXT,
                inventaris_bron TEXT NOT NULL,
                actief INTEGER NOT NULL,
                bijgewerkt_op TEXT NOT NULL
            )
            """
        )
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS rar_inventory_items (
                id INTEGER PRIMARY KEY,
                rar_set_key TEXT NOT NULL,
                rar_startbestand TEXT NOT NULL,
                verwacht_rel_pad TEXT NOT NULL,
                verwacht_rel_pad_norm TEXT NOT NULL,
                verwachte_map TEXT NOT NULL,
                verwachte_bestandsnaam TEXT NOT NULL,
                verwachte_grootte INTEGER,
                verwachte_crc32 TEXT,
                verwachte_modified TEXT,
                inventaris_bron TEXT NOT NULL,
                listing_fout TEXT,
                aangetroffen_rel_pad TEXT,
                ontbreekt INTEGER NOT NULL DEFAULT 1,
                grootte_afwijkend INTEGER NOT NULL DEFAULT 0,
                gekoppeld_op TEXT,
                FOREIGN KEY (rar_set_key)
                    REFERENCES rar_sets (rar_set_key)
                    ON DELETE CASCADE,
                UNIQUE (rar_set_key, verwacht_rel_pad_norm)
            )
            """
        )
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_items (
                id INTEGER PRIMARY KEY,
                rar_set_key TEXT NOT NULL,
                verwacht_rel_pad TEXT NOT NULL,
                verwacht_rel_pad_norm TEXT NOT NULL,
                probleem_type TEXT NOT NULL,
                probleem_bron TEXT NOT NULL,
                verwachte_grootte INTEGER,
                verwachte_crc32 TEXT,
                mp3_id INTEGER,
                inventaris_id INTEGER,
                ffmpeg_fout TEXT,
                rar_fout TEXT,
                feit_ontbreekt INTEGER NOT NULL DEFAULT 0,
                feit_rar_crc INTEGER NOT NULL DEFAULT 0,
                feit_corrupt INTEGER NOT NULL DEFAULT 0,
                feit_nul_bytes INTEGER NOT NULL DEFAULT 0,
                feit_grootte_afwijking INTEGER NOT NULL DEFAULT 0,
                spotify_verwerkt INTEGER NOT NULL DEFAULT 0,
                download_verwerkt INTEGER NOT NULL DEFAULT 0,
                geplaatst INTEGER NOT NULL DEFAULT 0,
                bepaalde_artiest TEXT,
                bepaalde_titel TEXT,
                bepaald_album TEXT,
                bepaald_tracknummer TEXT,
                identiteit_bron TEXT,
                identiteit_betrouwbaarheid REAL,
                identiteit_bepaald_op TEXT,
                identiteit_bron_handtekening TEXT,
                identiteit_reden TEXT,
                aangemaakt_op TEXT NOT NULL,
                bijgewerkt_op TEXT NOT NULL,
                FOREIGN KEY (mp3_id)
                    REFERENCES mp3_bestanden (id)
                    ON DELETE SET NULL,
                FOREIGN KEY (inventaris_id)
                    REFERENCES rar_inventory_items (id)
                    ON DELETE CASCADE,
                UNIQUE (rar_set_key, verwacht_rel_pad_norm)
            )
            """
        )
        recovery_kolommen = {
            rij["name"]
            for rij in self.verbinding.execute(
                "PRAGMA table_info(recovery_items)"
            )
        }
        recovery_migraties = {
            "bepaalde_artiest": "TEXT",
            "bepaalde_titel": "TEXT",
            "bepaald_album": "TEXT",
            "bepaald_tracknummer": "TEXT",
            "identiteit_bron": "TEXT",
            "identiteit_betrouwbaarheid": "REAL",
            "identiteit_bepaald_op": "TEXT",
            "identiteit_bron_handtekening": "TEXT",
            "identiteit_reden": "TEXT",
        }

        for kolom, kolomtype in recovery_migraties.items():
            if kolom not in recovery_kolommen:
                self.verbinding.execute(
                    f"ALTER TABLE recovery_items "
                    f"ADD COLUMN {kolom} {kolomtype}"
                )
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_provider_resultaten (
                id INTEGER PRIMARY KEY,
                recovery_item_id INTEGER NOT NULL,
                relatief_pad TEXT,
                provider TEXT NOT NULL,
                provider_track_id TEXT,
                provider_url TEXT,
                gevonden_artiest TEXT,
                gevonden_titel TEXT,
                gevonden_album TEXT,
                gevonden_duur_ms INTEGER,
                zoekmethode TEXT NOT NULL,
                matchscore REAL,
                resultaat_type TEXT NOT NULL,
                foutmelding TEXT,
                gezocht_op TEXT NOT NULL,
                retry_na TEXT,
                identiteit_handtekening TEXT NOT NULL,
                FOREIGN KEY (recovery_item_id)
                    REFERENCES recovery_items (id)
                    ON DELETE CASCADE,
                UNIQUE (recovery_item_id, provider)
            )
            """
        )
        self.verbinding.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_recovery_provider_resultaat_type
            ON recovery_provider_resultaten (provider, resultaat_type)
            """
        )
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS par_inventory (
                id INTEGER PRIMARY KEY,
                par_set_key TEXT NOT NULL UNIQUE,
                gekoppelde_rar_set_key TEXT,
                par_startbestand TEXT,
                aantal_par_bestanden INTEGER NOT NULL DEFAULT 0,
                aantal_recovery_volumes INTEGER NOT NULL DEFAULT 0,
                recovery_blocks_beschikbaar INTEGER,
                recovery_blocks_benodigd INTEGER,
                status TEXT NOT NULL,
                verificatie_tool TEXT,
                verificatie_melding TEXT,
                bijgewerkt_op TEXT NOT NULL,
                FOREIGN KEY (gekoppelde_rar_set_key)
                    REFERENCES rar_sets (rar_set_key)
                    ON DELETE SET NULL
            )
            """
        )
        self.verbinding.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_par_inventory_rar_set
            ON par_inventory (gekoppelde_rar_set_key)
            """
        )
        self.verbinding.execute(
            """
            CREATE TABLE IF NOT EXISTS par_verifications (
                id INTEGER PRIMARY KEY,
                par_set_key TEXT NOT NULL UNIQUE,
                executable_path TEXT,
                executable_source TEXT,
                par2_file TEXT NOT NULL,
                command TEXT NOT NULL,
                return_code INTEGER,
                verification_status TEXT NOT NULL,
                verification_summary TEXT NOT NULL,
                stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '',
                verified_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                timed_out INTEGER NOT NULL DEFAULT 0,
                error_type TEXT,
                FOREIGN KEY (par_set_key)
                    REFERENCES par_inventory (par_set_key)
                    ON DELETE CASCADE
            )
            """
        )
        kolommen = {
            rij["name"]
            for rij in self.verbinding.execute(
                "PRAGMA table_info(provider_resultaten)"
            )
        }

        if "zoekmethode" not in kolommen:
            self.verbinding.execute(
                """
                ALTER TABLE provider_resultaten
                ADD COLUMN zoekmethode TEXT NOT NULL DEFAULT 'not_found'
                """
            )

        self.verbinding.commit()

    def nieuwe_scan(self):
        """
        Markeer eerdere MP3's als inactief en behoud providerresultaten.
        """

        self.verbinding.execute(
            "UPDATE mp3_bestanden SET bestaat = ?",
            (False,)
        )
        self.verbinding.commit()

    def __len__(self):
        rij = self.verbinding.execute(
            """
            SELECT COUNT(*) AS aantal
            FROM mp3_bestanden
            WHERE bestaat = ?
            """,
            (True,)
        ).fetchone()
        return rij["aantal"]

    def __contains__(self, relatief_pad):
        rij = self.verbinding.execute(
            """
            SELECT 1
            FROM mp3_bestanden
            WHERE relatief_pad = ? AND bestaat = ?
            LIMIT 1
            """,
            (relatief_pad, True)
        ).fetchone()
        return rij is not None

    def values(self):
        rijen = self.verbinding.execute(
            """
            SELECT *
            FROM mp3_bestanden
            WHERE bestaat = ?
            ORDER BY rowid
            """,
            (True,)
        ).fetchall()
        return [self._naar_dict(rij) for rij in rijen]

    def get(self, relatief_pad, standaard=None):
        rij = self.verbinding.execute(
            """
            SELECT *
            FROM mp3_bestanden
            WHERE relatief_pad = ? AND bestaat = ?
            """,
            (relatief_pad, True)
        ).fetchone()

        if rij is None:
            return standaard

        return self._naar_dict(rij)

    def sluit(self):
        self.verbinding.close()

    @staticmethod
    def _naar_dict(rij):
        return {
            "id": rij["id"],
            "bestand": Path(rij["bestand"]),
            "relatief_pad": rij["relatief_pad"],
            "bestaat": bool(rij["bestaat"]),
            "nul_bytes": bool(rij["nul_bytes"]),
            "rar": {
                "status": rij["rar_status"],
                "type": rij["rar_type"]
            },
            "ffmpeg": {
                "status": rij["ffmpeg_status"],
                "type": rij["ffmpeg_type"],
                "melding": rij["ffmpeg_melding"]
            }
        }


def maak_database(pad=DATABASE_BESTAND):
    """
    Maak automatisch een SQLite-database voor de huidige scan.
    """

    database = SQLiteDatabase(pad)
    database.nieuwe_scan()
    return database


def voeg_mp3_toe(database, basis_map, bestand):
    """
    Voeg een MP3-bestand toe aan de database.
    """

    relatief_pad = str(bestand.relative_to(basis_map))

    database.verbinding.execute(
        """
        INSERT INTO mp3_bestanden (
            relatief_pad,
            bestand,
            bestaat,
            nul_bytes,
            rar_status,
            rar_type,
            ffmpeg_status,
            ffmpeg_type,
            ffmpeg_melding
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (relatief_pad) DO UPDATE SET
            bestand = excluded.bestand,
            bestaat = excluded.bestaat,
            nul_bytes = excluded.nul_bytes,
            rar_status = excluded.rar_status,
            rar_type = excluded.rar_type,
            ffmpeg_status = excluded.ffmpeg_status,
            ffmpeg_type = excluded.ffmpeg_type,
            ffmpeg_melding = excluded.ffmpeg_melding
        """,
        (
            relatief_pad,
            str(bestand),
            True,
            False,
            "NIET_GECONTROLEERD",
            None,
            "NIET_GECONTROLEERD",
            None,
            None
        )
    )
    database.verbinding.execute(
        """
        UPDATE mp3_bestanden
        SET id = rowid
        WHERE relatief_pad = ? AND id IS NULL
        """,
        (relatief_pad,)
    )
    database.verbinding.commit()


def zet_nul_bytes(database, basis_map, bestand):
    """
    Markeer een bestand als 0-byte.
    """

    relatief_pad = str(bestand.relative_to(basis_map))

    database.verbinding.execute(
        """
        UPDATE mp3_bestanden
        SET nul_bytes = ?
        WHERE relatief_pad = ?
        """,
        (True, relatief_pad)
    )
    database.verbinding.commit()


def zet_rar_status(database, relatief_pad, status, fouttype=None):
    """
    Sla de uitslag van de RAR-controle op.
    """

    database.verbinding.execute(
        """
        UPDATE mp3_bestanden
        SET rar_status = ?, rar_type = ?
        WHERE relatief_pad = ?
        """,
        (status, fouttype, relatief_pad)
    )
    database.verbinding.commit()


def zet_ffmpeg_status(database, relatief_pad, status, fouttype=None, melding=None):
    """
    Sla de uitslag van de FFmpeg-controle op.
    """

    database.verbinding.execute(
        """
        UPDATE mp3_bestanden
        SET ffmpeg_status = ?, ffmpeg_type = ?, ffmpeg_melding = ?
        WHERE relatief_pad = ?
        """,
        (status, fouttype, melding, relatief_pad)
    )
    database.verbinding.commit()


def verkrijg_mp3(database, relatief_pad):
    """
    Geef de database-entry van een MP3 terug.
    """

    return database.get(relatief_pad)


def bewaar_provider_resultaat(
    database,
    relatief_pad,
    provider,
    zoek_artiest,
    zoek_titel,
    gevonden,
    track_id=None,
    url=None,
    artiest=None,
    titel=None,
    album=None,
    duur_ms=None,
    zoekmethode="not_found"
):
    """
    Bewaar een gevonden of niet-gevonden resultaat van een muziekprovider.
    """

    database.verbinding.execute(
        """
        INSERT INTO provider_resultaten (
            relatief_pad,
            provider,
            zoek_artiest,
            zoek_titel,
            gevonden,
            track_id,
            url,
            artiest,
            titel,
            album,
            duur_ms,
            zoekmethode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (relatief_pad, provider) DO UPDATE SET
            zoek_artiest = excluded.zoek_artiest,
            zoek_titel = excluded.zoek_titel,
            gevonden = excluded.gevonden,
            track_id = excluded.track_id,
            url = excluded.url,
            artiest = excluded.artiest,
            titel = excluded.titel,
            album = excluded.album,
            duur_ms = excluded.duur_ms,
            zoekmethode = excluded.zoekmethode
        """,
        (
            relatief_pad,
            provider,
            zoek_artiest,
            zoek_titel,
            gevonden,
            track_id,
            url,
            artiest,
            titel,
            album,
            duur_ms,
            zoekmethode
        )
    )
    database.verbinding.commit()


def verkrijg_provider_resultaat(database, relatief_pad, provider):
    """
    Geef het opgeslagen resultaat van een muziekprovider terug.
    """

    rij = database.verbinding.execute(
        """
        SELECT *
        FROM provider_resultaten
        WHERE relatief_pad = ? AND provider = ?
        """,
        (relatief_pad, provider)
    ).fetchone()

    if rij is None:
        return None

    return {
        "relatief_pad": rij["relatief_pad"],
        "provider": rij["provider"],
        "zoek_artiest": rij["zoek_artiest"],
        "zoek_titel": rij["zoek_titel"],
        "gevonden": bool(rij["gevonden"]),
        "track_id": rij["track_id"],
        "url": rij["url"],
        "artiest": rij["artiest"],
        "titel": rij["titel"],
        "album": rij["album"],
        "duur_ms": rij["duur_ms"],
        "zoekmethode": rij["zoekmethode"]
    }


def begin_rar_inventory_scan(database):
    """
    Markeer bestaande RAR-sets als inactief voor een nieuwe inventarisrun.
    """

    database.verbinding.execute(
        "UPDATE rar_sets SET actief = ?",
        (False,)
    )
    database.verbinding.commit()


def bewaar_rar_set(
    database,
    rar_set_key,
    rar_startbestand,
    listing_volledig,
    listing_fout=None,
    inventaris_bron="7zip-slt"
):
    """
    Bewaar de uitkomst van één RAR-listing.
    """

    database.verbinding.execute(
        """
        INSERT INTO rar_sets (
            rar_set_key,
            rar_startbestand,
            listing_volledig,
            listing_fout,
            inventaris_bron,
            actief,
            bijgewerkt_op
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (rar_set_key) DO UPDATE SET
            rar_startbestand = excluded.rar_startbestand,
            listing_volledig = excluded.listing_volledig,
            listing_fout = excluded.listing_fout,
            inventaris_bron = excluded.inventaris_bron,
            actief = excluded.actief,
            bijgewerkt_op = excluded.bijgewerkt_op
        """,
        (
            rar_set_key,
            str(rar_startbestand),
            listing_volledig,
            listing_fout,
            inventaris_bron,
            True,
            datetime.now().isoformat(timespec="seconds")
        )
    )
    database.verbinding.commit()


def vervang_rar_inventory_items(
    database,
    rar_set_key,
    rar_startbestand,
    items,
    listing_fout=None,
    inventaris_bron="7zip-slt"
):
    """
    Upsert de actuele MP3-inventaris en verwijder verouderde setitems.
    """

    actuele_sleutels = set()

    for item in items:
        sleutel = item["verwacht_rel_pad_norm"]
        actuele_sleutels.add(sleutel)
        database.verbinding.execute(
            """
            INSERT INTO rar_inventory_items (
                rar_set_key,
                rar_startbestand,
                verwacht_rel_pad,
                verwacht_rel_pad_norm,
                verwachte_map,
                verwachte_bestandsnaam,
                verwachte_grootte,
                verwachte_crc32,
                verwachte_modified,
                inventaris_bron,
                listing_fout,
                aangetroffen_rel_pad,
                ontbreekt,
                grootte_afwijkend,
                gekoppeld_op
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, 0, NULL)
            ON CONFLICT (rar_set_key, verwacht_rel_pad_norm) DO UPDATE SET
                rar_startbestand = excluded.rar_startbestand,
                verwacht_rel_pad = excluded.verwacht_rel_pad,
                verwachte_map = excluded.verwachte_map,
                verwachte_bestandsnaam = excluded.verwachte_bestandsnaam,
                verwachte_grootte = excluded.verwachte_grootte,
                verwachte_crc32 = excluded.verwachte_crc32,
                verwachte_modified = excluded.verwachte_modified,
                inventaris_bron = excluded.inventaris_bron,
                listing_fout = excluded.listing_fout,
                aangetroffen_rel_pad = NULL,
                ontbreekt = 1,
                grootte_afwijkend = 0,
                gekoppeld_op = NULL
            """,
            (
                rar_set_key,
                str(rar_startbestand),
                item["verwacht_rel_pad"],
                sleutel,
                item["verwachte_map"],
                item["verwachte_bestandsnaam"],
                item.get("verwachte_grootte"),
                item.get("verwachte_crc32"),
                item.get("verwachte_modified"),
                inventaris_bron,
                listing_fout
            )
        )

    bestaande_sleutels = {
        rij["verwacht_rel_pad_norm"]
        for rij in database.verbinding.execute(
            """
            SELECT verwacht_rel_pad_norm
            FROM rar_inventory_items
            WHERE rar_set_key = ?
            """,
            (rar_set_key,)
        )
    }

    for verouderde_sleutel in bestaande_sleutels - actuele_sleutels:
        database.verbinding.execute(
            """
            DELETE FROM rar_inventory_items
            WHERE rar_set_key = ? AND verwacht_rel_pad_norm = ?
            """,
            (rar_set_key, verouderde_sleutel)
        )

    database.verbinding.commit()


def eindig_rar_inventory_scan(database):
    """
    Verwijder sets die niet in de huidige RAR-map voorkwamen.
    """

    database.verbinding.execute(
        "DELETE FROM rar_sets WHERE actief = ?",
        (False,)
    )
    database.verbinding.commit()


def vergelijk_rar_inventory(database):
    """
    Koppel verwachte RAR-items aan actieve, werkelijk aanwezige MP3's.
    """

    aangetroffen = {}

    for gegevens in database.values():
        relatief_pad = gegevens["relatief_pad"]
        sleutel = normaliseer_relatief_pad_sleutel(relatief_pad)
        bestand = Path(gegevens["bestand"])

        try:
            grootte = bestand.stat().st_size
        except OSError:
            continue

        aangetroffen[sleutel] = {
            "relatief_pad": relatief_pad,
            "grootte": grootte
        }

    items = database.verbinding.execute(
        """
        SELECT inventaris.id, inventaris.verwacht_rel_pad_norm,
               inventaris.verwachte_grootte
        FROM rar_inventory_items AS inventaris
        JOIN rar_sets AS sets
          ON sets.rar_set_key = inventaris.rar_set_key
        WHERE sets.actief = ?
        """,
        (True,)
    ).fetchall()
    gekoppeld_op = datetime.now().isoformat(timespec="seconds")

    for item in items:
        werkelijk = aangetroffen.get(item["verwacht_rel_pad_norm"])

        if werkelijk is None:
            database.verbinding.execute(
                """
                UPDATE rar_inventory_items
                SET aangetroffen_rel_pad = NULL,
                    ontbreekt = 1,
                    grootte_afwijkend = 0,
                    gekoppeld_op = NULL
                WHERE id = ?
                """,
                (item["id"],)
            )
            continue

        verwachte_grootte = item["verwachte_grootte"]
        grootte_afwijkend = (
            verwachte_grootte is not None
            and werkelijk["grootte"] != verwachte_grootte
        )
        database.verbinding.execute(
            """
            UPDATE rar_inventory_items
            SET aangetroffen_rel_pad = ?,
                ontbreekt = 0,
                grootte_afwijkend = ?,
                gekoppeld_op = ?
            WHERE id = ?
            """,
            (
                werkelijk["relatief_pad"],
                grootte_afwijkend,
                gekoppeld_op,
                item["id"]
            )
        )

    database.verbinding.commit()


def verkrijg_rar_inventory_overzicht(database):
    """
    Geef de actuele inventaristellingen terug.
    """

    rij = database.verbinding.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM rar_sets WHERE actief = 1)
                AS rar_sets,
            COUNT(inventaris.id) AS verwachte_mp3s,
            COALESCE(SUM(CASE WHEN inventaris.ontbreekt = 0
                              THEN 1 ELSE 0 END), 0)
                AS aangetroffen_mp3s,
            COALESCE(SUM(CASE WHEN inventaris.ontbreekt = 1
                              THEN 1 ELSE 0 END), 0)
                AS ontbrekende_mp3s,
            COALESCE(SUM(CASE WHEN inventaris.grootte_afwijkend = 1
                              THEN 1 ELSE 0 END), 0)
                AS grootte_afwijkend,
            (SELECT COUNT(*) FROM rar_sets
             WHERE actief = 1 AND listing_volledig = 0)
                AS listing_fouten
        FROM rar_inventory_items AS inventaris
        JOIN rar_sets AS sets
          ON sets.rar_set_key = inventaris.rar_set_key
        WHERE sets.actief = 1
        """
    ).fetchone()
    return dict(rij)


def verkrijg_ontbrekende_rar_items(database):
    """
    Geef alle volledig ontbrekende MP3-items uit de actuele inventaris.
    """

    rijen = database.verbinding.execute(
        """
        SELECT inventaris.rar_set_key,
               inventaris.verwacht_rel_pad,
               inventaris.verwachte_grootte,
               inventaris.verwachte_crc32
        FROM rar_inventory_items AS inventaris
        JOIN rar_sets AS sets
          ON sets.rar_set_key = inventaris.rar_set_key
        WHERE sets.actief = 1 AND inventaris.ontbreekt = 1
        ORDER BY inventaris.rar_set_key,
                 inventaris.verwacht_rel_pad_norm
        """
    ).fetchall()
    return [dict(rij) for rij in rijen]


def verkrijg_recovery_overzicht(database):
    """
    Geef aantallen per hoofdprobleemtype terug.
    """

    rij = database.verbinding.execute(
        """
        SELECT
            COUNT(*) AS totaal,
            COALESCE(SUM(CASE WHEN probleem_type = 'ontbreekt'
                              THEN 1 ELSE 0 END), 0) AS ontbreekt,
            COALESCE(SUM(CASE WHEN probleem_type = 'corrupt'
                              THEN 1 ELSE 0 END), 0) AS corrupt,
            COALESCE(SUM(CASE WHEN probleem_type = 'nul_bytes'
                              THEN 1 ELSE 0 END), 0) AS nul_bytes,
            COALESCE(SUM(CASE WHEN probleem_type = 'grootte_afwijking'
                              THEN 1 ELSE 0 END), 0)
                AS grootte_afwijking,
            COALESCE(SUM(CASE WHEN probleem_type = 'rar_crc'
                              THEN 1 ELSE 0 END), 0) AS rar_crc
        FROM recovery_items
        """
    ).fetchone()
    return dict(rij)


def verkrijg_recovery_items(database):
    """
    Geef alle actuele recovery-items terug.
    """

    rijen = database.verbinding.execute(
        """
        SELECT *
        FROM recovery_items
        ORDER BY rar_set_key, verwacht_rel_pad_norm
        """
    ).fetchall()
    return [dict(rij) for rij in rijen]
