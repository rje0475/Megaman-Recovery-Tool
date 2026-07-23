import base64
import json
import os
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from database import bewaar_provider_resultaat
from database import verkrijg_provider_resultaat


SPOTIFY_PROVIDER = "spotify"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

ZOEKMETHODE_ORIGINAL = "original"
ZOEKMETHODE_CLEANED = "cleaned"
ZOEKMETHODE_NOT_FOUND = "not_found"

ONNODIGE_TERMEN = re.compile(
    r"""
    \b(?:
        official\s+(?:music\s+)?video
        |official\s+audio
        |lyric\s+video
        |lyrics?
        |hd
        |hq
    )\b
    """,
    re.IGNORECASE | re.VERBOSE
)
REMASTER_TERMEN = re.compile(
    r"""
    \b
    (?:(?:19|20)\d{2}\s+)?
    (?:digital(?:ly)?\s+)?
    remaster(?:ed)?
    (?:\s+(?:19|20)\d{2})?
    (?:\s+(?:version|edition))?
    \b
    """,
    re.IGNORECASE | re.VERBOSE
)
VOORLOOP_TRACKNUMMER = re.compile(
    r"^\s*\d{1,3}(?:\s*[._-]\s*|\s+)"
)


class SpotifyFout(RuntimeError):
    """
    Basisfout voor Spotify-configuratie en API-aanroepen.
    """


class SpotifyConfiguratieFout(SpotifyFout):
    """
    De Spotify-configuratie is niet compleet.
    """


class SpotifyApiFout(SpotifyFout):
    """
    De Spotify Web API kon de aanvraag niet verwerken.
    """


@dataclass(frozen=True)
class MuziekResultaat:
    """
    Provider-onafhankelijk zoekresultaat voor toekomstige muziekdiensten.
    """

    provider: str
    zoek_artiest: str
    zoek_titel: str
    gevonden: bool
    track_id: str | None = None
    url: str | None = None
    artiest: str | None = None
    titel: str | None = None
    album: str | None = None
    duur_ms: int | None = None
    zoekmethode: str | None = None


@dataclass(frozen=True)
class SpotifyScanResultaat:
    """
    Tellingen van één Spotify-verrijkingsstap.
    """

    totaal: int
    verwerkt: int
    gevonden: int
    niet_gevonden: int
    fouten: int
    overgeslagen: int
    credentials_ontbreken: bool = False


def schoon_mp3_bestandsnaam(bestandsnaam):
    """
    Maak een MP3-bestandsnaam geschikt voor een Spotify-zoekopdracht.
    """

    return _schoon_zoekveld(bestandsnaam, verwijder_tracknummer=True)


def schoon_spotify_zoekwaarden(artiest, titel):
    """
    Schoon artiest en titel op zonder betekenisvolle tekst te splitsen.
    """

    return (
        _schoon_zoekveld(artiest, verwijder_tracknummer=False),
        schoon_mp3_bestandsnaam(titel)
    )


def _schoon_zoekveld(waarde, verwijder_tracknummer):
    tekst = str(waarde).strip()
    tekst = re.sub(r"(?i)\.mp3$", "", tekst)
    tekst = tekst.replace("_", " ")

    if verwijder_tracknummer:
        tekst = VOORLOOP_TRACKNUMMER.sub("", tekst)

    tekst = ONNODIGE_TERMEN.sub(" ", tekst)
    tekst = REMASTER_TERMEN.sub(" ", tekst)
    tekst = re.sub(r"[\(\)\[\]\{\}]", " ", tekst)
    tekst = re.sub(r"\s+", " ", tekst).strip()
    tekst = re.sub(r"^(?:[-–—]\s*)+", "", tekst)
    tekst = re.sub(r"(?:\s*[-–—])+$", "", tekst)
    return tekst.strip()


class SpotifyClient:
    """
    Client voor cataloguszoekopdrachten via de officiële Spotify Web API.
    """

    def __init__(
        self,
        client_id,
        client_secret,
        market="NL",
        timeout=15,
        token_url=SPOTIFY_TOKEN_URL,
        api_url=SPOTIFY_API_URL
    ):
        if not client_id or not client_secret:
            raise SpotifyConfiguratieFout(
                "Spotify Client ID en Client Secret zijn verplicht."
            )

        self.client_id = client_id
        self.client_secret = client_secret
        self.market = market
        self.timeout = timeout
        self.token_url = token_url
        self.api_url = api_url.rstrip("/")
        self._toegangstoken = None
        self._token_verloopt_op = 0

    @classmethod
    def uit_omgeving(cls):
        """
        Lees Spotify-credentials uit omgevingsvariabelen.
        """

        return cls(
            os.environ.get("SPOTIFY_CLIENT_ID"),
            os.environ.get("SPOTIFY_CLIENT_SECRET")
        )

    def zoek_nummer(self, artiest, titel):
        """
        Zoek het beste Spotify-resultaat op artiest en titel.
        """

        artiest = artiest.strip()
        titel = titel.strip()

        if not artiest or not titel:
            raise ValueError("Artiest en titel mogen niet leeg zijn.")

        nummers = self.zoek_nummers(artiest, titel, limiet=1)

        if not nummers:
            return MuziekResultaat(
                provider=SPOTIFY_PROVIDER,
                zoek_artiest=artiest.strip(),
                zoek_titel=titel.strip(),
                gevonden=False
            )
        return nummers[0]

    def zoek_nummers(self, artiest, titel, limiet=10):
        """
        Zoek meerdere kandidaten zodat herstelmatching zelf kan rangschikken.
        """

        artiest = artiest.strip()
        titel = titel.strip()

        if not titel:
            raise ValueError("Titel mag niet leeg zijn.")

        parameters = {
            "q": (
                f"track:{titel} artist:{artiest}"
                if artiest else f"track:{titel}"
            ),
            "type": "track",
            "limit": max(1, min(int(limiet), 50))
        }

        if self.market:
            parameters["market"] = self.market

        data = self._verstuur_json(
            f"{self.api_url}/search?{urlencode(parameters)}",
            headers={
                "Authorization": f"Bearer {self._haal_toegangstoken()}"
            }
        )

        nummers = data.get("tracks", {}).get("items", [])

        return [
            MuziekResultaat(
                provider=SPOTIFY_PROVIDER,
                zoek_artiest=artiest,
                zoek_titel=titel,
                gevonden=True,
                track_id=nummer.get("id"),
                url=nummer.get("external_urls", {}).get("spotify"),
                artiest=", ".join(
                    uitvoerende.get("name", "")
                    for uitvoerende in nummer.get("artists", [])
                    if uitvoerende.get("name")
                ),
                titel=nummer.get("name"),
                album=nummer.get("album", {}).get("name"),
                duur_ms=nummer.get("duration_ms")
            )
            for nummer in nummers
        ]

    def _haal_toegangstoken(self):
        if (
            self._toegangstoken
            and time.monotonic() < self._token_verloopt_op
        ):
            return self._toegangstoken

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")

        data = self._verstuur_json(
            self.token_url,
            method="POST",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            body=urlencode({
                "grant_type": "client_credentials"
            }).encode("ascii")
        )

        toegangstoken = data.get("access_token")

        if not toegangstoken:
            raise SpotifyApiFout(
                "Spotify gaf geen access token terug."
            )

        geldigheid = int(data.get("expires_in", 3600))
        self._toegangstoken = toegangstoken
        self._token_verloopt_op = time.monotonic() + max(
            geldigheid - 30,
            0
        )
        return toegangstoken

    def _verstuur_json(
        self,
        url,
        method="GET",
        headers=None,
        body=None
    ):
        aanvraag = Request(
            url,
            data=body,
            headers=headers or {},
            method=method
        )

        try:
            with urlopen(aanvraag, timeout=self.timeout) as antwoord:
                return json.load(antwoord)
        except HTTPError as fout:
            melding = self._lees_api_fout(fout)
            raise SpotifyApiFout(
                f"Spotify API-fout {fout.code}: {melding}"
            ) from fout
        except URLError as fout:
            raise SpotifyApiFout(
                f"Spotify is niet bereikbaar: {fout.reason}"
            ) from fout
        except (json.JSONDecodeError, UnicodeDecodeError) as fout:
            raise SpotifyApiFout(
                "Spotify gaf een ongeldig JSON-antwoord terug."
            ) from fout

    @staticmethod
    def _lees_api_fout(fout):
        try:
            data = json.load(fout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return fout.reason

        api_fout = data.get("error", {})

        if isinstance(api_fout, dict):
            return api_fout.get("message", fout.reason)

        return str(api_fout)


def zoek_en_bewaar_spotify_nummer(
    database,
    relatief_pad,
    artiest,
    titel,
    client=None
):
    """
    Zoek een nummer bij Spotify en bewaar ook een niet-gevonden resultaat.
    """

    client = client or SpotifyClient.uit_omgeving()
    resultaat = client.zoek_nummer(artiest, titel)

    if resultaat.gevonden:
        resultaat = replace(
            resultaat,
            zoekmethode=ZOEKMETHODE_ORIGINAL
        )
    else:
        schone_artiest, schone_titel = schoon_spotify_zoekwaarden(
            artiest,
            titel
        )
        resultaat = client.zoek_nummer(
            schone_artiest or artiest.strip(),
            schone_titel or titel.strip()
        )
        resultaat = replace(
            resultaat,
            zoekmethode=(
                ZOEKMETHODE_CLEANED
                if resultaat.gevonden
                else ZOEKMETHODE_NOT_FOUND
            )
        )

    bewaar_provider_resultaat(
        database=database,
        relatief_pad=relatief_pad,
        provider=resultaat.provider,
        zoek_artiest=resultaat.zoek_artiest,
        zoek_titel=resultaat.zoek_titel,
        gevonden=resultaat.gevonden,
        track_id=resultaat.track_id,
        url=resultaat.url,
        artiest=resultaat.artiest,
        titel=resultaat.titel,
        album=resultaat.album,
        duur_ms=resultaat.duur_ms,
        zoekmethode=resultaat.zoekmethode
    )

    return resultaat


def voer_spotify_scan_uit(database, client=None, uitvoer=None):
    """
    Verrijk actieve, niet-lege MP3's en hervat eerdere Spotify-resultaten.
    """

    uitvoer = uitvoer or sys.stdout

    if client is None:
        try:
            client = SpotifyClient.uit_omgeving()
        except SpotifyConfiguratieFout:
            uitvoer.write(
                "Spotify overgeslagen: stel SPOTIFY_CLIENT_ID en "
                "SPOTIFY_CLIENT_SECRET in.\n"
            )
            return SpotifyScanResultaat(
                totaal=0,
                verwerkt=0,
                gevonden=0,
                niet_gevonden=0,
                fouten=0,
                overgeslagen=0,
                credentials_ontbreken=True
            )

    kandidaten = [
        gegevens
        for gegevens in database.values()
        if gegevens["bestaat"] and not gegevens["nul_bytes"]
    ]
    resterend = [
        gegevens
        for gegevens in kandidaten
        if verkrijg_provider_resultaat(
            database,
            gegevens["relatief_pad"],
            SPOTIFY_PROVIDER
        ) is None
    ]

    totaal = len(resterend)
    overgeslagen = len(kandidaten) - totaal
    verwerkt = 0
    gevonden = 0
    niet_gevonden = 0
    fouten = 0

    _toon_spotify_voortgang(
        uitvoer,
        verwerkt,
        totaal,
        gevonden,
        niet_gevonden,
        fouten
    )

    for gegevens in resterend:
        artiest, titel = bepaal_spotify_zoekwaarden(gegevens)

        try:
            resultaat = zoek_en_bewaar_spotify_nummer(
                database,
                gegevens["relatief_pad"],
                artiest,
                titel,
                client
            )
        except (SpotifyFout, ValueError):
            fouten += 1
        else:
            if resultaat.gevonden:
                gevonden += 1
            else:
                niet_gevonden += 1

        verwerkt += 1
        _toon_spotify_voortgang(
            uitvoer,
            verwerkt,
            totaal,
            gevonden,
            niet_gevonden,
            fouten
        )

    uitvoer.write("\n")

    if overgeslagen:
        uitvoer.write(
            f"Spotify: {overgeslagen} eerder verwerkte "
            "resultaten overgeslagen.\n"
        )

    return SpotifyScanResultaat(
        totaal=totaal,
        verwerkt=verwerkt,
        gevonden=gevonden,
        niet_gevonden=niet_gevonden,
        fouten=fouten,
        overgeslagen=overgeslagen
    )


def bepaal_spotify_zoekwaarden(gegevens):
    """
    Leid artiest en titel af uit het relatieve pad en de bestandsnaam.
    """

    relatief_pad = Path(gegevens["relatief_pad"])
    delen = relatief_pad.parts
    bestandsnaam = relatief_pad.name
    naam_zonder_extensie = re.sub(
        r"(?i)\.mp3$",
        "",
        bestandsnaam
    )
    naam_zonder_tracknummer = VOORLOOP_TRACKNUMMER.sub(
        "",
        naam_zonder_extensie
    )

    if " - " in naam_zonder_tracknummer:
        artiest, titel = naam_zonder_tracknummer.split(" - ", 1)
        return artiest.strip(), titel.strip()

    if len(delen) > 1:
        return delen[0].strip(), bestandsnaam

    artiest = Path(gegevens["bestand"]).parent.name.strip()
    return artiest or "Onbekend", bestandsnaam


def _toon_spotify_voortgang(
    uitvoer,
    verwerkt,
    totaal,
    gevonden,
    niet_gevonden,
    fouten
):
    uitvoer.write(
        f"\rSpotify: {verwerkt}/{totaal} | "
        f"gevonden: {gevonden} | "
        f"niet gevonden: {niet_gevonden} | "
        f"fouten: {fouten}"
    )
    uitvoer.flush()
