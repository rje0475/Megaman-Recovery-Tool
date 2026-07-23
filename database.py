import sqlite3
from pathlib import Path


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
