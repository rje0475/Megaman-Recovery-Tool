"""Globale exception hooks en compacte crashrapporten."""

import json
import logging
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path


class CrashReporter:
    def __init__(self, crash_dir="logs/crash", user_notifier=None):
        self.crash_dir = Path(crash_dir)
        self.user_notifier = user_notifier
        self.logger = logging.getLogger(__name__)

    def report(self, exc_type, exc_value, exc_traceback, thread_name=None):
        if issubclass(exc_type, KeyboardInterrupt):
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "exception": exc_type.__name__, "message": str(exc_value),
            "thread": thread_name or threading.current_thread().name,
            "python": platform.python_version(), "platform": platform.platform(),
            "traceback": "".join(traceback.format_exception(
                exc_type, exc_value, exc_traceback
            )),
        }
        try:
            self.crash_dir.mkdir(parents=True, exist_ok=True)
            target = self.crash_dir / f"crash_{timestamp}.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            target = None
        self.logger.critical("Onverwachte applicatiefout: %s", exc_value,
                             exc_info=(exc_type, exc_value, exc_traceback))
        if self.user_notifier:
            try:
                self.user_notifier(
                    "Megaman Recovery Tool kon een onverwachte fout veilig afhandelen. "
                    "Controleer het crashrapport in logs/crash."
                )
            except Exception:
                self.logger.exception("Crashmelding kon niet worden getoond")
        return target


def install_global_exception_handler(reporter=None, user_notifier=None):
    """Installeer hooks; ``user_notifier`` is de enige publieke notifier-API."""
    reporter = reporter or CrashReporter(user_notifier=user_notifier)
    if user_notifier is not None:
        reporter.user_notifier = user_notifier

    def system_hook(exc_type, exc_value, exc_traceback):
        reporter.report(exc_type, exc_value, exc_traceback)

    def thread_hook(args):
        reporter.report(args.exc_type, args.exc_value, args.exc_traceback,
                        getattr(args.thread, "name", None))

    sys.excepthook = system_hook
    threading.excepthook = thread_hook
    return reporter
