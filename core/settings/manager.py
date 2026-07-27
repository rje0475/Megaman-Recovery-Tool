"""Centrale, atomisch opgeslagen JSON SettingsManager."""

import json
import os
import shutil
import subprocess
from datetime import datetime
from threading import RLock
from copy import deepcopy
from pathlib import Path

from .defaults import DEFAULTS
from .migration import migrate
from .models import SettingsSnapshot


class SettingsValidationError(ValueError):
    pass


class SettingsManager:
    def __init__(self, path=None, environment=None, create=True):
        self.path = Path(path or "config/settings.json")
        self._lock = RLock()
        self.environment = os.environ if environment is None else environment
        raw = self._read_or_recover() if self.path.is_file() else {}
        self._data = migrate(raw)
        self._apply_legacy_environment()
        if create and (not self.path.is_file() or raw != self._data):
            self.save()

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SettingsValidationError(f"Instellingen kunnen niet worden gelezen: {error}") from error

    def _read_or_recover(self):
        try:
            return self._read()
        except SettingsValidationError:
            backup = self.path.with_name(
                f"{self.path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
            )
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return {}

    def _apply_legacy_environment(self):
        mapping = {
            "SPOTIFY_CLIENT_ID": ("spotify", "client_id"),
            "SPOTIFY_CLIENT_SECRET": ("spotify", "client_secret"),
            "YOUTUBE_API_KEY": ("youtube", "api_key"),
            "FFMPEG_PATH": ("audio", "ffmpeg_path"),
            "FFPROBE_PATH": ("audio", "ffprobe_path"),
            "OUTPUT_AUDIO_FORMAT": ("audio", "output_format"),
            "MP3_BITRATE_KBPS": ("audio", "bitrate_kbps"),
            "PROCESSING_TIMEOUT_SECONDS": ("audio", "processing_timeout_seconds"),
            "KEEP_SOURCE_AFTER_PROCESSING": ("audio", "keep_source"),
            "OUTPUT_ROOT": ("download", "output_root"),
            "FILENAME_TEMPLATE": ("metadata", "filename_template"),
            "FOLDER_TEMPLATE": ("metadata", "folder_template"),
            "COLLISION_POLICY": ("metadata", "collision_policy"),
        }
        for env_name, (section, key) in mapping.items():
            value = self.environment.get(env_name)
            if value not in (None, ""):
                self._data[section][key] = value

        keep = self._data["audio"]["keep_source"]
        if isinstance(keep, str):
            self._data["audio"]["keep_source"] = keep.casefold() not in {"0", "false", "no"}

    @property
    def settings(self):
        return SettingsSnapshot.from_dict(deepcopy(self._data))

    def section(self, name):
        return deepcopy(self._data[name])

    def update_section(self, name, values):
        with self._lock:
            candidate = deepcopy(self._data)
            candidate[name].update(values)
            self.validate(candidate)
            self._data = candidate
            self.save()

    def save(self):
        with self._lock:
            self.validate(self._data)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)

    def reset(self):
        self._data = deepcopy(DEFAULTS)
        self.save()

    def export(self, target):
        exported = deepcopy(self._data)
        exported["spotify"].pop("client_secret", None)
        exported["youtube"].pop("api_key", None)
        Path(target).write_text(json.dumps(exported, indent=2, ensure_ascii=False), encoding="utf-8")

    def import_file(self, source):
        incoming = migrate(json.loads(Path(source).read_text(encoding="utf-8")))
        # Lokale secrets blijven behouden wanneer de export ze terecht niet bevat.
        for section, key in (("spotify", "client_secret"), ("youtube", "api_key")):
            if not incoming[section].get(key):
                incoming[section][key] = self._data[section].get(key, "")
        self.validate(incoming)
        self._data = incoming
        self.save()

    @staticmethod
    def validate(data):
        if data["general"]["theme"] not in {"light", "dark", "system"}:
            raise SettingsValidationError("Thema moet light, dark of system zijn.")
        if data["metadata"]["collision_policy"] not in {"Rename", "Overwrite", "Skip"}:
            raise SettingsValidationError("Collision policy moet Rename, Overwrite of Skip zijn.")
        for section, key, minimum in (("audio", "bitrate_kbps", 32),
                                      ("download", "retries", 0),
                                      ("youtube", "max_candidates", 1)):
            try:
                if int(data[section][key]) < minimum:
                    raise ValueError
            except (TypeError, ValueError):
                raise SettingsValidationError(f"Ongeldige waarde voor {section}.{key}.")
        for template in (data["metadata"]["filename_template"], data["metadata"]["folder_template"]):
            if not str(template).strip():
                raise SettingsValidationError("Bestands- en maptemplates mogen niet leeg zijn.")
        return True

    def diagnostics(self):
        import sqlite3, sys
        try:
            import mutagen
            mutagen_version = getattr(mutagen, "version_string", "onbekend")
        except ImportError:
            mutagen_version = "niet geïnstalleerd"
        try:
            import yt_dlp
            yt_version = yt_dlp.version.__version__
        except ImportError:
            yt_version = "niet geïnstalleerd"
        audio = self._data["audio"]
        return {
            "Python": sys.version.split()[0], "SQLite": sqlite3.sqlite_version,
            "Mutagen": str(mutagen_version), "yt-dlp": yt_version,
            "FFmpeg": self._tool_status(audio["ffmpeg_path"], "ffmpeg"),
            "ffprobe": self._tool_status(audio["ffprobe_path"], "ffprobe"),
            "Spotify auth": "geconfigureerd" if self._data["spotify"]["client_id"] else "niet geconfigureerd",
            "YouTube API": "geconfigureerd" if self._data["youtube"]["api_key"] else "niet geconfigureerd",
        }

    def validate_external(self):
        messages = []
        audio = self._data["audio"]
        for label, configured in (("FFmpeg", audio["ffmpeg_path"]),
                                  ("ffprobe", audio["ffprobe_path"])):
            if configured and not Path(configured).is_file():
                messages.append(f"{label}-bestand bestaat niet: {configured}")
        for key in ("download_dir", "processed_dir", "output_root"):
            path = Path(self._data["download"][key])
            existing = next((candidate for candidate in (path, *path.parents)
                             if candidate.exists()), None)
            if not existing or not os.access(existing, os.W_OK):
                messages.append(f"Map is niet schrijfbaar: {path}")
        client_id = self._data["spotify"]["client_id"]
        if client_id and len(client_id) < 10:
            messages.append("Spotify Client ID lijkt ongeldig.")
        api_key = self._data["youtube"]["api_key"]
        if api_key and len(api_key) < 20:
            messages.append("YouTube API-key lijkt ongeldig.")
        return tuple(messages)

    @staticmethod
    def _tool_status(configured, executable):
        found = configured or shutil.which(executable)
        if not found or not Path(found).is_file():
            return "niet gevonden"
        try:
            result = subprocess.run(
                [str(found), "-version"], capture_output=True, text=True,
                timeout=5, check=False,
            )
            first_line = (result.stdout or result.stderr).splitlines()[0]
            return first_line.strip() or str(found)
        except (OSError, subprocess.SubprocessError, IndexError):
            return f"gevonden: {found}"


_default_manager = None


def get_settings_manager():
    global _default_manager
    if _default_manager is None:
        _default_manager = SettingsManager()
    return _default_manager
