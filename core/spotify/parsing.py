import logging
import re
from dataclasses import dataclass


LOGGER = logging.getLogger(__name__)
_HITLIJSTCODE = re.compile(r"^(?P<code>\d{8})\s+(?P<naam>\S.*)$")
_SCHEIDING = re.compile(r"\s+-\s+")


@dataclass(frozen=True)
class ParsedRecoveryName:
    original_name: str
    chart_code: str | None
    artist: str
    title: str

    @property
    def free_query(self):
        return f"{self.artist} {self.title}".strip()


def _bestandsstam(naam):
    bestandsnaam = str(naam or "").replace("\\", "/").rsplit("/", 1)[-1]
    if bestandsnaam.casefold().endswith(".mp3"):
        return bestandsnaam[:-4]
    return bestandsnaam


def parseer_recovery_itemnaam(
    originele_naam, artiest=None, titel=None
):
    origineel = _bestandsstam(originele_naam)
    code = None
    naam_zonder_code = origineel
    overeenkomst = _HITLIJSTCODE.match(origineel)
    if overeenkomst:
        code = overeenkomst.group("code")
        naam_zonder_code = overeenkomst.group("naam").strip()

    geparseerde_artiest = str(artiest or "").strip()
    geparseerde_titel = str(titel or "").strip()
    if code:
        delen = _SCHEIDING.split(naam_zonder_code, maxsplit=1)
        if len(delen) == 2 and all(delen):
            geparseerde_artiest, geparseerde_titel = map(str.strip, delen)
        else:
            artiest_match = _HITLIJSTCODE.match(geparseerde_artiest)
            if artiest_match:
                geparseerde_artiest = artiest_match.group("naam").strip()

    resultaat = ParsedRecoveryName(
        origineel, code, geparseerde_artiest, geparseerde_titel
    )
    LOGGER.debug("Originele naam:\n%s", resultaat.original_name)
    LOGGER.debug(
        "Verwijderde hitlijstcode:\n%s",
        resultaat.chart_code or "(geen)",
    )
    LOGGER.debug("Geparseerde artiest:\n%s", resultaat.artist)
    LOGGER.debug("Geparseerde titel:\n%s", resultaat.title)
    LOGGER.debug("Spotify-query:\n%s", resultaat.free_query)
    return resultaat
