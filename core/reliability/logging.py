"""Centrale, roterende en geheimen-redacterende logging."""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock


_SECRET = re.compile(
    r"(?i)(access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|api[_ -]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


class _RedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = _SECRET.sub(r"\1\2[REDACTED]", record.getMessage())
        record.args = ()
        return True


class LoggingManager:
    _lock = RLock()

    def __init__(self, log_dir="logs", level="INFO", max_bytes=2_000_000,
                 backup_count=5):
        self.log_dir = Path(log_dir)
        self.level = getattr(logging, str(level).upper(), logging.INFO)
        self.max_bytes = int(max_bytes)
        self.backup_count = int(backup_count)

    def configure(self):
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            root = logging.getLogger()
            root.setLevel(self.level)
            marker = str((self.log_dir / "megaman-recovery.log").resolve())
            if any(getattr(handler, "_megaman_path", None) == marker
                   for handler in root.handlers):
                return root
            handler = RotatingFileHandler(
                marker, maxBytes=self.max_bytes, backupCount=self.backup_count,
                encoding="utf-8",
            )
            handler._megaman_path = marker
            handler.setLevel(self.level)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)s | %(threadName)s | "
                "%(name)s | %(message)s"
            ))
            handler.addFilter(_RedactionFilter())
            root.addHandler(handler)
            return root

    def close(self):
        marker = str((self.log_dir / "megaman-recovery.log").resolve())
        with self._lock:
            root = logging.getLogger()
            for handler in tuple(root.handlers):
                if getattr(handler, "_megaman_path", None) == marker:
                    root.removeHandler(handler)
                    handler.close()

    @staticmethod
    def get_logger(name):
        return logging.getLogger(name)
