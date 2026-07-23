import base64
import json
import os
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from database import bewaar_provider_resultaat


SPOTIFY_PROVIDER = "spotify"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"


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
            os.environ.get("SPOTIFY_CLIENT_SECRET"),
            market=os.environ.get("SPOTIFY_MARKET", "NL")
        )

    def zoek_nummer(self, artiest, titel):
        """
        Zoek het beste Spotify-resultaat op artiest en titel.
        """

        artiest = artiest.strip()
        titel = titel.strip()

        if not artiest or not titel:
            raise ValueError("Artiest en titel mogen niet leeg zijn.")

        parameters = {
            "q": f"track:{titel} artist:{artiest}",
            "type": "track",
            "limit": 1
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

        if not nummers:
            return MuziekResultaat(
                provider=SPOTIFY_PROVIDER,
                zoek_artiest=artiest,
                zoek_titel=titel,
                gevonden=False
            )

        nummer = nummers[0]

        return MuziekResultaat(
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
        duur_ms=resultaat.duur_ms
    )

    return resultaat
