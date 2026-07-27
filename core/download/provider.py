"""Downloadproviderinterface en yt-dlp-implementatie zonder conversie."""

from abc import ABC, abstractmethod
from pathlib import Path
from threading import Event

from core.download.errors import (
    DownloadCancelled, ProviderDownloadError, classify_download_error,
)
from core.download.models import (
    DownloadProgress, PreparedDownload, ProviderDownloadResult,
)


class DownloadProvider(ABC):
    @abstractmethod
    def prepare(self, job, workspace):
        raise NotImplementedError

    @abstractmethod
    def download(self, prepared, progress_callback=None):
        raise NotImplementedError

    @abstractmethod
    def cancel(self):
        raise NotImplementedError

    @abstractmethod
    def cleanup(self, prepared):
        raise NotImplementedError


class YtDlpDownloadProvider(DownloadProvider):
    """Downloadt de beste onbewerkte audiobron via de yt-dlp Python-API."""

    def __init__(self, ydl_factory=None, socket_timeout=30):
        self._cancelled = Event()
        self.socket_timeout = socket_timeout
        self.ydl_factory = ydl_factory or self._default_factory

    @staticmethod
    def _default_factory(options):
        try:
            import yt_dlp
        except ImportError as error:
            raise ProviderDownloadError(
                "yt-dlp ontbreekt; installeer de projectrequirements."
            ) from error
        return yt_dlp.YoutubeDL(options)

    def prepare(self, job, workspace):
        if not job.source_url:
            raise ProviderDownloadError("Downloadjob heeft geen YouTube-URL.")
        workspace = Path(workspace) / job.job_id
        workspace.mkdir(parents=True, exist_ok=True)
        self._cancelled.clear()
        return PreparedDownload(
            job_id=job.job_id,
            source_url=job.source_url,
            workspace=workspace,
            output_template=workspace / "source_audio.%(ext)s",
        )

    def download(self, prepared, progress_callback=None):
        def progress_hook(data):
            if self._cancelled.is_set():
                raise DownloadCancelled("Download geannuleerd.")
            if data.get("status") != "downloading":
                return
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes") or 0
            percent = downloaded * 100 / total if total else 0.0
            if progress_callback:
                progress_callback(DownloadProgress(
                    percent=percent,
                    speed_bytes_per_second=data.get("speed"),
                    eta_seconds=data.get("eta"),
                    total_bytes=total,
                    downloaded_bytes=downloaded,
                ))

        options = {
            "format": "bestaudio/best",
            "outtmpl": str(prepared.output_template),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": self.socket_timeout,
            "progress_hooks": [progress_hook],
            # Bewust geen postprocessors, conversie of FFmpeg-configuratie.
        }
        try:
            with self.ydl_factory(options) as ydl:
                info = ydl.extract_info(prepared.source_url, download=True)
                path = self._result_path(ydl, info, prepared.workspace)
        except BaseException as error:
            if isinstance(error, (SystemExit, GeneratorExit)):
                raise
            raise classify_download_error(error) from error
        if self._cancelled.is_set():
            raise DownloadCancelled("Download geannuleerd.")
        return ProviderDownloadResult(Path(path), info or {})

    def cancel(self):
        self._cancelled.set()

    def cleanup(self, prepared):
        for path in prepared.workspace.iterdir():
            if not path.is_file():
                continue
            try:
                path.unlink()
            except OSError:
                pass
        try:
            prepared.workspace.rmdir()
        except OSError:
            pass

    @staticmethod
    def _result_path(ydl, info, workspace):
        requested = (info or {}).get("requested_downloads") or ()
        for item in requested:
            if item.get("filepath"):
                return Path(item["filepath"])
        filename = (info or {}).get("filepath") or (info or {}).get("_filename")
        if filename:
            return Path(filename)
        try:
            candidate = Path(ydl.prepare_filename(info))
            if candidate.exists():
                return candidate
        except Exception:
            pass
        files = tuple(
            path for path in Path(workspace).glob("source_audio.*")
            if path.is_file() and path.suffix.casefold() != ".part"
        )
        return files[0] if files else Path(workspace) / "source_audio"
