"""Centrale release- en buildinformatie."""

import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

APP_NAME = "Megaman Recovery Tool"
__version__ = "1.0.0"
BUILD_NUMBER = os.environ.get("MEGAMAN_BUILD_NUMBER", "1")
BUILD_DATE = os.environ.get("MEGAMAN_BUILD_DATE", str(date.today()))


def git_commit(cwd=None):
    configured = os.environ.get("MEGAMAN_GIT_COMMIT")
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=Path(cwd or Path(__file__).resolve().parents[1]),
            capture_output=True, text=True, timeout=2, check=False,
        )
        return result.stdout.strip() or "onbekend"
    except (OSError, subprocess.SubprocessError):
        return "onbekend"


@dataclass(frozen=True)
class VersionInfo:
    name: str = APP_NAME
    version: str = __version__
    build: str = BUILD_NUMBER
    build_date: str = BUILD_DATE
    commit: str = ""

    @classmethod
    def current(cls):
        return cls(commit=git_commit())

    @property
    def display(self):
        return f"{self.name} {self.version} (build {self.build})"
