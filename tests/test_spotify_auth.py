import io
import json
import logging
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from core.spotify.auth import (
    SpotifyCallbackTimeout,
    SpotifyUserAuthorizationError,
    autoriseer_spotify_gebruiker,
    verkrijg_geldig_gebruikerstoken,
)


ENVIRONMENT = {
    "SPOTIFY_CLIENT_ID": "client-id-geheim",
    "SPOTIFY_CLIENT_SECRET": "client-secret-geheim",
}


class MemoryTokenStore:
    def __init__(self, token=None):
        self.token = token
        self.saved = []

    def load(self):
        return self.token

    def save(self, token):
        self.token = dict(token)
        self.saved.append(dict(token))


class JsonResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class QueueOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        antwoord = self.responses.pop(0)
        if isinstance(antwoord, Exception):
            raise antwoord
        return JsonResponse(antwoord)


def succesvolle_callback(code="authorization-code"):
    def ontvang(url, state, browser_open, timeout):
        browser_open(url)
        return {"state": state, "code": code}

    return ontvang


class SpotifyUserAuthorizationTest(unittest.TestCase):
    def test_eerste_login_bewaart_tokens_en_opent_browser(self):
        store = MemoryTokenStore()
        opener = QueueOpener({
            "access_token": "access-geheim",
            "refresh_token": "refresh-geheim",
            "expires_in": 3600,
        })
        urls = []
        token = autoriseer_spotify_gebruiker(
            environment=ENVIRONMENT, token_store=store,
            opener=opener, browser_open=lambda url: urls.append(url) or True,
            callback_receiver=succesvolle_callback(),
            state_factory=lambda lengte: "veilige-state",
            now=lambda: 1000,
        )
        self.assertEqual(token, "access-geheim")
        self.assertEqual(store.token["refresh_token"], "refresh-geheim")
        self.assertEqual(store.token["expires_at"], 4600)
        self.assertIn("state=veilige-state", urls[0])
        self.assertIn(
            "redirect_uri=http%3A%2F%2F127.0.0.1%3A8888%2Fcallback",
            urls[0],
        )
        self.assertIn("playlist-read-private", urls[0])

    def test_geldig_token_wordt_zonder_netwerk_hergebruikt(self):
        store = MemoryTokenStore({
            "access_token": "nog-geldig",
            "refresh_token": "refresh",
            "expires_at": 5000,
        })
        token = verkrijg_geldig_gebruikerstoken(
            environment=ENVIRONMENT, token_store=store,
            opener=lambda *args: self.fail("geen netwerk verwacht"),
            now=lambda: 1000,
        )
        self.assertEqual(token, "nog-geldig")
        self.assertEqual(store.saved, [])

    def test_verlopen_token_wordt_vernieuwd(self):
        store = MemoryTokenStore({
            "access_token": "oud",
            "refresh_token": "blijvend-refresh",
            "expires_at": 900,
        })
        opener = QueueOpener({
            "access_token": "vernieuwd",
            "refresh_token": "nieuw-refresh",
            "expires_in": 1800,
        })
        token = verkrijg_geldig_gebruikerstoken(
            environment=ENVIRONMENT, token_store=store,
            opener=opener, now=lambda: 1000,
        )
        self.assertEqual(token, "vernieuwd")
        self.assertEqual(store.token["refresh_token"], "nieuw-refresh")
        self.assertIn(
            b"grant_type=refresh_token", opener.requests[0][0].data
        )

    def test_bestaand_refresh_token_blijft_zonder_nieuwe_waarde(self):
        store = MemoryTokenStore({
            "access_token": "oud",
            "refresh_token": "bewaren",
            "expires_at": 900,
        })
        token = verkrijg_geldig_gebruikerstoken(
            environment=ENVIRONMENT, token_store=store,
            opener=QueueOpener({
                "access_token": "nieuw",
                "expires_in": 3600,
            }),
            now=lambda: 1000,
        )
        self.assertEqual(token, "nieuw")
        self.assertEqual(store.token["refresh_token"], "bewaren")

    def test_foutieve_state_wordt_geweigerd(self):
        def verkeerde_state(url, state, browser_open, timeout):
            return {"state": "aanvaller", "code": "code"}

        with self.assertRaisesRegex(
            SpotifyUserAuthorizationError, "state-parameter"
        ):
            autoriseer_spotify_gebruiker(
                environment=ENVIRONMENT,
                token_store=MemoryTokenStore(),
                opener=QueueOpener({}),
                callback_receiver=verkeerde_state,
                state_factory=lambda lengte: "verwacht",
            )

    def test_callback_timeout_wordt_doorgemeld(self):
        def timeout(*args):
            raise SpotifyCallbackTimeout("timeout")

        with self.assertRaises(SpotifyCallbackTimeout):
            autoriseer_spotify_gebruiker(
                environment=ENVIRONMENT,
                token_store=MemoryTokenStore(),
                callback_receiver=timeout,
            )

    def test_refreshfout_start_opnieuw_browserlogin(self):
        refresh_error = HTTPError(
            "https://accounts.spotify.com/api/token",
            400, "Bad Request", {}, None,
        )
        opener = QueueOpener(
            refresh_error,
            {
                "access_token": "na-login",
                "refresh_token": "nieuw-refresh",
                "expires_in": 3600,
            },
        )
        store = MemoryTokenStore({
            "access_token": "oud",
            "refresh_token": "ongeldig",
            "expires_at": 0,
        })
        token = verkrijg_geldig_gebruikerstoken(
            environment=ENVIRONMENT, token_store=store,
            opener=opener,
            callback_receiver=succesvolle_callback(),
            browser_open=lambda url: True,
            now=lambda: 1000,
        )
        self.assertEqual(token, "na-login")
        self.assertEqual(len(opener.requests), 2)

    def test_secrets_en_tokens_worden_niet_gelogd(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        oude_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            verkrijg_geldig_gebruikerstoken(
                environment=ENVIRONMENT,
                token_store=MemoryTokenStore({
                    "access_token": "access-geheim",
                    "refresh_token": "refresh-geheim",
                    "expires_at": 5000,
                }),
                now=lambda: 1000,
            )
        finally:
            root.removeHandler(handler)
            root.setLevel(oude_level)
        log = stream.getvalue()
        for geheim in (
            "client-id-geheim", "client-secret-geheim",
            "access-geheim", "refresh-geheim",
        ):
            self.assertNotIn(geheim, log)


if __name__ == "__main__":
    unittest.main()
