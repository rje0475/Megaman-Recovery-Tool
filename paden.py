import unicodedata
from pathlib import PureWindowsPath


def normaliseer_relatief_pad(pad):
    """
    Normaliseer een relatief pad zonder betekenisvolle onderdelen te wijzigen.
    """

    tekst = unicodedata.normalize(
        "NFC", str(pad).strip().replace("/", "\\")
    )
    windows_pad = PureWindowsPath(tekst)

    if windows_pad.is_absolute() or windows_pad.drive:
        raise ValueError(f"Geen relatief pad: {pad}")

    delen = [
        deel
        for deel in windows_pad.parts
        if deel not in ("", ".")
    ]

    if not delen or ".." in delen:
        raise ValueError(f"Ongeldig relatief pad: {pad}")

    return str(PureWindowsPath(*delen))


def normaliseer_relatief_pad_sleutel(pad):
    """
    Maak een hoofdletterongevoelige sleutel van een relatief pad.
    """

    return normaliseer_relatief_pad(pad).casefold()
