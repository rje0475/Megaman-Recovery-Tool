import base64
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.spotify.models import SpotifyConfig, SpotifyTrack


TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"


class SpotifyConfigurationError(RuntimeError):
    pass


class SpotifyApiError(RuntimeError):
    pass


class SpotifyClient:
    def __init__(
        self, config: SpotifyConfig, opener=urlopen,
        token_url=TOKEN_URL, api_url=API_URL,
    ):
        if not config.client_id or not config.client_secret:
            raise SpotifyConfigurationError(
                "SPOTIFY_CLIENT_ID en SPOTIFY_CLIENT_SECRET zijn verplicht."
            )
        self.config = config
        self.opener = opener
        self.token_url = token_url
        self.api_url = api_url.rstrip("/")
        self._token = None
        self._token_expires = 0.0

    @classmethod
    def from_environment(cls, environment=None, **kwargs):
        environment = os.environ if environment is None else environment
        return cls(
            SpotifyConfig(
                environment.get("SPOTIFY_CLIENT_ID", ""),
                environment.get("SPOTIFY_CLIENT_SECRET", ""),
                environment.get("SPOTIFY_MARKET", "NL"),
                access_token=environment.get("SPOTIFY_ACCESS_TOKEN"),
            ),
            **kwargs,
        )

    def search_tracks(self, query, limit=20):
        parameters = {
            "q": query,
            "type": "track",
            "limit": max(1, min(int(limit), 50)),
        }
        if self.config.market:
            parameters["market"] = self.config.market
        data = self._json_request(
            f"{self.api_url}/search?{urlencode(parameters)}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        return tuple(
            SpotifyTrack(
                track_id=item["id"],
                uri=item.get("uri"),
                url=item.get("external_urls", {}).get("spotify"),
                album=item.get("album", {}).get("name"),
                artists=tuple(
                    artist["name"] for artist in item.get("artists", ())
                    if artist.get("name")
                ),
                title=item.get("name", ""),
                duration_ms=item.get("duration_ms"),
                popularity=item.get("popularity"),
            )
            for item in data.get("tracks", {}).get("items", ())
            if item.get("id") and item.get("name")
        )

    def get_playlist(self, playlist_id):
        try:
            return self._user_json_request(
                f"/playlists/{playlist_id}"
            )
        except SpotifyApiError as error:
            if "Spotify API-fout 404:" in str(error):
                return None
            raise

    def list_current_user_playlists(self):
        playlists = []
        url = f"{self.api_url}/me/playlists?limit=50"
        while url:
            data = self._user_json_request(url, absolute=True)
            playlists.extend(data.get("items", ()))
            url = data.get("next")
        return tuple(playlists)

    def create_playlist(
        self, name, description, public=False
    ):
        return self._user_json_request(
            "/me/playlists",
            method="POST",
            body=json.dumps({
                "name": name,
                "description": description,
                "public": bool(public),
            }).encode("utf-8"),
        )

    def get_playlist_track_ids(self, playlist_id):
        track_ids = set()
        url = (
            f"{self.api_url}/playlists/{playlist_id}/items"
            "?limit=50"
        )
        while url:
            data = self._user_json_request(url, absolute=True)
            for item in data.get("items", ()):
                track = item.get("track") or item.get("item") or {}
                if track.get("id"):
                    track_ids.add(track["id"])
            url = data.get("next")
        return frozenset(track_ids)

    def add_playlist_items(self, playlist_id, uris):
        uris = tuple(uris)
        for begin in range(0, len(uris), 100):
            self._user_json_request(
                f"/playlists/{playlist_id}/items",
                method="POST",
                body=json.dumps({
                    "uris": uris[begin:begin + 100]
                }).encode("utf-8"),
            )

    def _user_json_request(
        self, path, method="GET", body=None, absolute=False
    ):
        token = self.config.access_token
        if not token:
            raise SpotifyConfigurationError(
                "SPOTIFY_ACCESS_TOKEN is verplicht voor playlistbeheer."
            )
        url = path if absolute else f"{self.api_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self._json_request(
            url, method=method, headers=headers, body=body
        )

    def _access_token(self):
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        credentials = base64.b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode()
        ).decode("ascii")
        data = self._json_request(
            self.token_url,
            method="POST",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=b"grant_type=client_credentials",
        )
        self._token = data.get("access_token")
        if not self._token:
            raise SpotifyApiError("Spotify gaf geen access token terug.")
        self._token_expires = (
            time.monotonic() + max(0, int(data.get("expires_in", 3600)) - 30)
        )
        return self._token

    def _json_request(self, url, method="GET", headers=None, body=None):
        request = Request(
            url, data=body, headers=headers or {}, method=method
        )
        try:
            with self.opener(
                request, timeout=self.config.timeout
            ) as response:
                return json.load(response)
        except HTTPError as error:
            raise SpotifyApiError(
                f"Spotify API-fout {error.code}: {error.reason}"
            ) from error
        except URLError as error:
            raise SpotifyApiError(
                f"Spotify is niet bereikbaar: {error.reason}"
            ) from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SpotifyApiError(
                "Spotify gaf een ongeldig JSON-antwoord terug."
            ) from error
