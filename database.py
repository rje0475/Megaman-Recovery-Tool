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
        self.verbinding.commit()

    def nieuwe_scan(self):
        """
        Begin met een lege scanset, zoals de eerdere dictionary.
        """

        self.verbinding.execute("DELETE FROM mp3_bestanden")
        self.verbinding.commit()

    def __len__(self):
        rij = self.verbinding.execute(
            "SELECT COUNT(*) AS aantal FROM mp3_bestanden"
        ).fetchone()
        return rij["aantal"]

    def __contains__(self, relatief_pad):
        rij = self.verbinding.execute(
            """
            SELECT 1
            FROM mp3_bestanden
            WHERE relatief_pad = ?
            LIMIT 1
            """,
            (relatief_pad,)
        ).fetchone()
        return rij is not None

    def values(self):
        rijen = self.verbinding.execute(
            "SELECT * FROM mp3_bestanden ORDER BY rowid"
        ).fetchall()
        return [self._naar_dict(rij) for rij in rijen]

    def get(self, relatief_pad, standaard=None):
        rij = self.verbinding.execute(
            "SELECT * FROM mp3_bestanden WHERE relatief_pad = ?",
            (relatief_pad,)
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
        INSERT OR IGNORE INTO mp3_bestanden (
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
