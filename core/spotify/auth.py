import base64
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = ("playlist-read-private", "playlist-modify-private")
DEFAULT_TIMEOUT = 180
EXPIRY_MARGIN = 60


class SpotifyUserAuthorizationError(RuntimeError):
    pass


class SpotifyCallbackTimeout(SpotifyUserAuthorizationError):
    pass


class SpotifyTokenStore:
    def __init__(self, path=None, environment=None):
        environment = os.environ if environment is None else environment
        if path is None:
            configured = environment.get("SPOTIFY_TOKEN_CACHE")
            if configured:
                path = configured
            else:
                basis = environment.get("LOCALAPPDATA")
                basis = Path(basis) if basis else Path.home() / ".local"
                path = (
                    basis / "Megaman Recovery Tool"
                    / "spotify_user_tokens.json"
                )
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def save(self, token):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tijdelijk = self.path.with_suffix(self.path.suffix + ".tmp")
        tijdelijk.write_text(
            json.dumps(token, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            tijdelijk.chmod(0o600)
        except OSError:
            pass
        os.replace(tijdelijk, self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


def _configuratie(environment):
    client_id = environment.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = environment.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SpotifyUserAuthorizationError(
            "SPOTIFY_CLIENT_ID en SPOTIFY_CLIENT_SECRET zijn verplicht."
        )
    return client_id, client_secret


def _token_request(opener, client_id, client_secret, parameters):
    credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("ascii")
    request = Request(
        TOKEN_URL,
        data=urlencode(parameters).encode("ascii"),
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=15) as response:
            data = json.load(response)
    except HTTPError as error:
        raise SpotifyUserAuthorizationError(
            f"Spotify-autorisatie is mislukt (HTTP {error.code})."
        ) from error
    except URLError as error:
        raise SpotifyUserAuthorizationError(
            "Spotify-autorisatie is niet bereikbaar."
        ) from error
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SpotifyUserAuthorizationError(
            "Spotify gaf een ongeldig tokenantwoord."
        ) from error
    if not data.get("access_token"):
        raise SpotifyUserAuthorizationError(
            "Spotify gaf geen access token terug."
        )
    return data


def _normaliseer_token(data, bestaand_refresh_token=None, now=time.time):
    refresh_token = data.get("refresh_token") or bestaand_refresh_token
    return {
        "access_token": data["access_token"],
        "refresh_token": refresh_token,
        "expires_at": int(now()) + int(data.get("expires_in", 3600)),
    }


def _callback_handler(verwachte_state, resultaat):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            parameters = parse_qs(url.query)
            ontvangen_state = parameters.get("state", [None])[0]
            code = parameters.get("code", [None])[0]
            fout = parameters.get("error", [None])[0]
            geldig = (
                url.path == "/callback"
                and ontvangen_state
                and secrets.compare_digest(
                    ontvangen_state, verwachte_state
                )
                and code
                and not fout
            )
            resultaat.update({
                "path": url.path,
                "state": ontvangen_state,
                "code": code,
                "error": fout,
                "valid": bool(geldig),
            })
            self.send_response(200 if geldig else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            boodschap = (
                "Spotify-autorisatie gelukt. Dit venster mag worden gesloten."
                if geldig else
                "Spotify-autorisatie mislukt. Ga terug naar de applicatie."
            )
            self.wfile.write(
                f"<html><body><p>{boodschap}</p></body></html>"
                .encode("utf-8")
            )

        def log_message(self, format, *args):
            return

    return CallbackHandler


def _wacht_op_callback(
    authorization_url, state, browser_open, timeout
):
    resultaat = {}
    server = HTTPServer(
        ("127.0.0.1", 8888), _callback_handler(state, resultaat)
    )
    server.timeout = timeout
    try:
        if not browser_open(authorization_url):
            raise SpotifyUserAuthorizationError(
                "De Spotify-loginpagina kon niet worden geopend."
            )
        server.handle_request()
    finally:
        server.server_close()
    if not resultaat:
        raise SpotifyCallbackTimeout(
            "Geen Spotify-callback ontvangen binnen de timeout."
        )
    return resultaat


def autoriseer_spotify_gebruiker(
    environment=None, token_store=None, opener=urlopen,
    browser_open=webbrowser.open, callback_receiver=None,
    timeout=DEFAULT_TIMEOUT, state_factory=secrets.token_urlsafe,
    now=time.time,
):
    environment = os.environ if environment is None else environment
    client_id, client_secret = _configuratie(environment)
    store = token_store or SpotifyTokenStore(environment=environment)
    state = state_factory(32)
    authorization_url = f"{AUTHORIZE_URL}?{urlencode({
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': REDIRECT_URI,
        'scope': ' '.join(SCOPES),
        'state': state,
    })}"
    receiver = callback_receiver or _wacht_op_callback
    callback = receiver(
        authorization_url, state, browser_open, timeout
    )
    ontvangen_state = callback.get("state")
    if (
        not ontvangen_state
        or not secrets.compare_digest(ontvangen_state, state)
    ):
        raise SpotifyUserAuthorizationError(
            "Ongeldige Spotify state-parameter; autorisatie geweigerd."
        )
    if callback.get("error") or not callback.get("code"):
        raise SpotifyUserAuthorizationError(
            "Spotify-autorisatie is geweigerd of onvolledig."
        )
    data = _token_request(
        opener, client_id, client_secret, {
            "grant_type": "authorization_code",
            "code": callback["code"],
            "redirect_uri": REDIRECT_URI,
        },
    )
    token = _normaliseer_token(data, now=now)
    store.save(token)
    return token["access_token"]


def verkrijg_geldig_gebruikerstoken(
    environment=None, token_store=None, opener=urlopen,
    browser_open=webbrowser.open, callback_receiver=None,
    timeout=DEFAULT_TIMEOUT, now=time.time,
):
    environment = os.environ if environment is None else environment
    store = token_store or SpotifyTokenStore(environment=environment)
    token = store.load()
    if (
        token
        and token.get("access_token")
        and int(token.get("expires_at") or 0)
        > int(now()) + EXPIRY_MARGIN
    ):
        return token["access_token"]
    if token and token.get("refresh_token"):
        client_id, client_secret = _configuratie(environment)
        try:
            data = _token_request(
                opener, client_id, client_secret, {
                    "grant_type": "refresh_token",
                    "refresh_token": token["refresh_token"],
                },
            )
            vernieuwd = _normaliseer_token(
                data, token["refresh_token"], now
            )
            store.save(vernieuwd)
            return vernieuwd["access_token"]
        except SpotifyUserAuthorizationError:
            pass
    return autoriseer_spotify_gebruiker(
        environment=environment,
        token_store=store,
        opener=opener,
        browser_open=browser_open,
        callback_receiver=callback_receiver,
        timeout=timeout,
        now=now,
    )
