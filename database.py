def maak_database():
    """
    Maak een lege database voor alle MP3-bestanden.
    """

    return {}


def voeg_mp3_toe(database, basis_map, bestand):
    """
    Voeg een MP3-bestand toe aan de database.
    """

    sleutel = str(bestand.relative_to(basis_map))

    if sleutel in database:
        return

    database[sleutel] = {
        "bestand": bestand,
        "relatief_pad": sleutel,
        "bestaat": True,
        "nul_bytes": False,
        "rar": {
            "status": "NIET_GECONTROLEERD",
            "type": None
        },
        "ffmpeg": {
            "status": "NIET_GECONTROLEERD",
            "type": None,
            "melding": None
        }
    }


def zet_nul_bytes(database, basis_map, bestand):
    """
    Markeer een bestand als 0-byte.
    """

    sleutel = str(bestand.relative_to(basis_map))

    if sleutel in database:
        database[sleutel]["nul_bytes"] = True


def zet_rar_status(database, relatief_pad, status, fouttype=None):
    """
    Sla de uitslag van de RAR-controle op.
    """

    if relatief_pad not in database:
        return

    database[relatief_pad]["rar"]["status"] = status
    database[relatief_pad]["rar"]["type"] = fouttype


def zet_ffmpeg_status(database, relatief_pad, status, fouttype=None, melding=None):
    """
    Sla de uitslag van de FFmpeg-controle op.
    """

    if relatief_pad not in database:
        return

    database[relatief_pad]["ffmpeg"]["status"] = status
    database[relatief_pad]["ffmpeg"]["type"] = fouttype
    database[relatief_pad]["ffmpeg"]["melding"] = melding


def verkrijg_mp3(database, relatief_pad):
    """
    Geef de database-entry van een MP3 terug.
    """

    return database.get(relatief_pad)