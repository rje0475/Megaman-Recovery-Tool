"""Resultaat- en voortgangsmodellen voor downloads."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DownloadProgress:
    percent: float
    speed_bytes_per_second: float | None = None
    eta_seconds: int | None = None
    total_bytes: int | None = None
    downloaded_bytes: int | None = None
    stage: str = "DOWNLOADING"


@dataclass(frozen=True)
class PreparedDownload:
    job_id: str
    source_url: str
    workspace: Path
    output_template: Path


@dataclass(frozen=True)
class ProviderDownloadResult:
    path: Path
    metadata: dict


@dataclass(frozen=True)
class DownloadResult:
    job_id: str
    path: Path
    size: int
    duration_seconds: float
    status: str = "DOWNLOADED"
