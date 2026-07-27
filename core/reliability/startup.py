"""Vroege startuptrace en laatste zichtbare foutfallback voor windowed builds."""

import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from threading import RLock


def startup_log_path(environment=None):
    env = os.environ if environment is None else environment
    base = env.get("LOCALAPPDATA")
    if base:
        return Path(base) / "MegamanRecoveryTool" / "logs" / "startup.log"
    return Path(env.get("TEMP") or tempfile.gettempdir()) / "MegamanRecoveryTool-startup.log"


class StartupTrace:
    def __init__(self, path=None):
        self.path = Path(path or startup_log_path())
        self._explicit_path = path is not None
        self._lock = RLock()

    def write(self, message):
        line = f"{datetime.now().isoformat(timespec='milliseconds')} {message}\n"
        with self._lock:
            candidates = [self.path]
            fallback = Path(tempfile.gettempdir()) / "MegamanRecoveryTool-startup.log"
            if not self._explicit_path and fallback != self.path:
                candidates.append(fallback)
            for candidate in candidates:
                try:
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    with candidate.open("a", encoding="utf-8") as stream:
                        stream.write(line)
                        stream.flush()
                        try:
                            os.fsync(stream.fileno())
                        except OSError:
                            pass
                    self.path = candidate
                    return True
                except Exception:
                    continue
            return False

    def exception(self, context, error):
        self.write(f"FATAL {context}: {type(error).__name__}: {error}")
        self.write("".join(traceback.format_exception(
            type(error), error, error.__traceback__
        )).rstrip())


_trace = None


def get_startup_trace():
    global _trace
    if _trace is None:
        _trace = StartupTrace()
    return _trace


def show_fatal_startup_error(error, log_path=None, qapplication=None,
                             messagebox=None):
    """Toon best-effort Qt, anders een native Windows MessageBoxW."""
    path = Path(log_path or startup_log_path())
    safe = str(error or "onbekende fout").replace("\n", " ")[:300]
    text = (
        "Megaman Recovery Tool kon niet starten.\n\n"
        f"Fout: {safe}\n\nStartup-log: {path}"
    )
    if qapplication is not None:
        try:
            if messagebox is None:
                from PySide6.QtWidgets import QMessageBox
                messagebox = QMessageBox
            messagebox.critical(None, "Megaman Recovery Tool", text)
            return "qt"
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Megaman Recovery Tool", 0x10)
            return "native"
        except Exception:
            pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(text + "\n"); sys.stderr.flush()
            return "stderr"
    except Exception:
        pass
    return None
