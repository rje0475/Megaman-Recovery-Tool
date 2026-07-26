"""Spotify-coverdownload met persistente URL-cache."""

import hashlib
from pathlib import Path
from urllib.request import Request, urlopen

from .errors import ArtworkDownloadError, ArtworkMissingError


class ArtworkCache:
    def __init__(self, root, opener=urlopen, timeout=30):
        self.root = Path(root)
        self.opener = opener
        self.timeout = timeout

    def get(self, url):
        if not url:
            raise ArtworkMissingError("De gekozen Spotify-match heeft geen albumcover.")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{hashlib.sha256(url.encode()).hexdigest()}.jpg"
        if target.is_file() and self._is_jpeg(target.read_bytes()):
            return target
        temporary = target.with_suffix(".tmp")
        try:
            response = self.opener(Request(url, headers={"User-Agent": "Megaman-Recovery-Tool"}), timeout=self.timeout)
            data = response.read()
            if not self._is_jpeg(data):
                raise ArtworkDownloadError("Spotify-albumcover is geen geldige JPEG.")
            temporary.write_bytes(data)
            temporary.replace(target)
            return target
        except ArtworkDownloadError:
            raise
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raise ArtworkDownloadError(f"Spotify-albumcover kon niet worden opgehaald: {error}") from error

    @staticmethod
    def _is_jpeg(data):
        return len(data) > 4 and data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
