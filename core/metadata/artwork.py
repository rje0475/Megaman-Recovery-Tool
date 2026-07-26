"""Spotify-coverdownload met persistente URL-cache."""

import hashlib
import time
from pathlib import Path
from urllib.request import Request, urlopen

from .errors import ArtworkDownloadError, ArtworkMissingError


class ArtworkCache:
    def __init__(self, root, opener=urlopen, timeout=30, max_files=500,
                 max_age_days=90):
        self.root = Path(root)
        self.opener = opener
        self.timeout = timeout
        self.max_files = max(1, int(max_files))
        self.max_age_seconds = max(1, int(max_age_days)) * 86400

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
            self.cleanup()
            return target
        except ArtworkDownloadError:
            raise
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raise ArtworkDownloadError(f"Spotify-albumcover kon niet worden opgehaald: {error}") from error

    @staticmethod
    def _is_jpeg(data):
        return len(data) > 4 and data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")

    def cleanup(self, now=None):
        """Verwijder alleen verlopen/overtallige cachebestanden, nooit output."""
        now = time.time() if now is None else float(now)
        try:
            files = sorted(
                (path for path in self.root.glob("*.jpg") if path.is_file()),
                key=lambda path: path.stat().st_mtime, reverse=True,
            )
        except OSError:
            return 0
        removed = 0
        for index, path in enumerate(files):
            try:
                expired = now - path.stat().st_mtime > self.max_age_seconds
                if expired or index >= self.max_files:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed
