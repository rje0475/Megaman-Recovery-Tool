"""Orkestreert één tijdelijke bronaudiodownload en verificatie."""

import time
from pathlib import Path

from core.download.errors import (
    DownloadCancelled, DownloadVerificationError, classify_download_error,
)
from core.download.models import DownloadResult
from core.download_queue import (
    CANCELLED, COMPLETED, DOWNLOADING, FAILED, PREPARING, VERIFYING,
)


class DownloadEngine:
    def __init__(self, manager, provider, temp_root, log_callback=None):
        self.manager = manager
        self.provider = provider
        self.temp_root = Path(temp_root)
        self.log_callback = log_callback or (lambda _message: None)
        self.current_job_id = None

    def execute(self, job, progress_callback=None):
        self.current_job_id = job.job_id
        started = time.monotonic()
        prepared = None
        try:
            self.manager.transition(job.job_id, PREPARING, "Download voorbereiden", 0)
            prepared = self.provider.prepare(job, self.temp_root)
            self.manager.transition(job.job_id, DOWNLOADING, "Download gestart", 0)
            self.log_callback(f"Download gestart: {job.artist} - {job.title}")

            def progress(update):
                self.manager.update_download_progress(job.job_id, update)
                if progress_callback:
                    progress_callback(job.job_id, update)

            provider_result = self.provider.download(prepared, progress)
            self.manager.transition(job.job_id, VERIFYING, "Download verifiëren", 99)
            path, size = self.verify(provider_result.path)
            duration = time.monotonic() - started
            self.manager.complete_download(job.job_id, path, size, duration)
            self.log_callback(f"Download voltooid: {path}")
            return DownloadResult(job.job_id, path, size, duration)
        except DownloadCancelled as error:
            if prepared:
                self.provider.cleanup(prepared)
            self.manager.transition(
                job.job_id, CANCELLED, "Download geannuleerd",
                error_code=error.code, error_message=str(error),
            )
            self.log_callback(f"Download geannuleerd: {job.job_id}")
            raise
        except BaseException as error:
            if isinstance(error, (SystemExit, GeneratorExit)):
                raise
            classified = classify_download_error(error)
            if prepared:
                self.provider.cleanup(prepared)
            self.manager.fail_download(job.job_id, classified.code, str(classified))
            self.log_callback(f"Download mislukt: {classified}")
            raise classified from error
        finally:
            self.current_job_id = None

    def cancel(self):
        self.provider.cancel()

    @staticmethod
    def verify(path):
        path = Path(path)
        try:
            if not path.is_file():
                raise DownloadVerificationError("Downloadbestand ontbreekt.")
            size = path.stat().st_size
            if size <= 0:
                raise DownloadVerificationError("Downloadbestand is leeg.")
            with path.open("rb") as stream:
                if not stream.read(1):
                    raise DownloadVerificationError("Downloadbestand is niet leesbaar.")
        except DownloadVerificationError:
            raise
        except OSError as error:
            raise DownloadVerificationError(
                f"Downloadbestand kan niet worden gelezen: {error}"
            ) from error
        return path, size
