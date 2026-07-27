"""Providerinterface en officiële YouTube Data API v3-implementatie."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.youtube.models import YouTubeVideo


API_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeConfigurationError(RuntimeError):
    pass


class YouTubeApiError(RuntimeError):
    def __init__(self, message, status_code=None, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _duration_seconds(value):
    if not value:
        return None
    match = re.fullmatch(
        r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?", value
    )
    if not match:
        return None
    return (
        int(match.group("h") or 0) * 3600
        + int(match.group("m") or 0) * 60
        + int(match.group("s") or 0)
    )


class YouTubeSearchProvider:
    """Zoekt zonder scraping via de officiële YouTube Data API."""

    def __init__(self, api_key, opener=urlopen, api_url=API_URL, timeout=15,
                 default_limit=10):
        if not str(api_key or "").strip():
            raise YouTubeConfigurationError(
                "YOUTUBE_API_KEY ontbreekt; configureer de officiële YouTube API-sleutel."
            )
        self.api_key = api_key
        self.opener = opener
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.default_limit = max(1, min(int(default_limit), 10))

    @classmethod
    def from_environment(cls, environment=None, **kwargs):
        from core.settings import SettingsManager, get_settings_manager
        manager = (get_settings_manager() if environment is None else
                   SettingsManager(path=os.devnull, environment=environment, create=False))
        settings = manager.section("youtube")
        kwargs.setdefault("timeout", 15)
        kwargs.setdefault("default_limit", settings["max_candidates"])
        return cls(settings["api_key"], **kwargs)

    @staticmethod
    def build_watch_url(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"

    def search(self, query, limit=None):
        limit = self.default_limit if limit is None else limit
        limit = max(1, min(int(limit), 10))
        data = self._get("search", {
            "part": "snippet", "type": "video", "q": query,
            "maxResults": limit,
        })
        ids = [
            item.get("id", {}).get("videoId")
            for item in data.get("items", ())
            if item.get("id", {}).get("videoId")
        ]
        if not ids:
            return ()
        details = self._get("videos", {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(ids),
        })
        by_id = {
            item.get("id"): self.normalize_result(item)
            for item in details.get("items", ()) if item.get("id")
        }
        return tuple(by_id[video_id] for video_id in ids if video_id in by_id)

    def normalize_result(self, item):
        snippet = item.get("snippet") or {}
        statistics = item.get("statistics") or {}
        thumbnails = snippet.get("thumbnails") or {}
        thumbnail = next((
            (thumbnails.get(key) or {}).get("url")
            for key in ("high", "medium", "default")
            if (thumbnails.get(key) or {}).get("url")
        ), None)
        video_id = item["id"]
        try:
            views = int(statistics["viewCount"])
        except (KeyError, TypeError, ValueError):
            views = None
        return YouTubeVideo(
            video_id=video_id,
            url=self.build_watch_url(video_id),
            title=snippet.get("title", ""),
            channel_name=snippet.get("channelTitle", ""),
            duration_seconds=_duration_seconds(
                (item.get("contentDetails") or {}).get("duration")
            ),
            published_at=snippet.get("publishedAt"),
            view_count=views,
            thumbnail_url=thumbnail,
        )

    def _get(self, endpoint, parameters):
        url = f"{self.api_url}/{endpoint}?{urlencode({**parameters, 'key': self.api_key})}"
        try:
            with self.opener(Request(url), timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as error:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            message = "YouTube API-aanvraag mislukt"
            if error.code == 403:
                message = "YouTube API-sleutel ongeldig of quota overschreden"
            elif error.code == 429:
                message = "YouTube API-limiet bereikt"
            raise YouTubeApiError(message, error.code, retry_after) from error
        except URLError as error:
            raise YouTubeApiError(f"YouTube is niet bereikbaar: {error.reason}") from error
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
            raise YouTubeApiError("YouTube gaf een ongeldig antwoord terug") from error
