"""Read-only project health check en interne self-test."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.settings import get_settings_manager
from database import DATABASE_BESTAND, SQLiteDatabase


@dataclass(frozen=True)
class HealthItem:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class HealthReport:
    items: tuple[HealthItem, ...]

    @property
    def status(self):
        statuses = {item.status for item in self.items}
        return "FAIL" if "FAIL" in statuses else "WARNING" if "WARNING" in statuses else "PASS"


def run_health_check(database_path=DATABASE_BESTAND, settings_manager=None):
    settings = settings_manager or get_settings_manager()
    items = []
    database_path = Path(database_path)
    if database_path.is_file():
        try:
            database = SQLiteDatabase(database_path)
            ok, messages = database.integrity_check(); database.sluit()
            items.append(HealthItem("Database", "PASS" if ok else "FAIL", "; ".join(messages)))
        except Exception as error: items.append(HealthItem("Database", "FAIL", str(error)))
    else: items.append(HealthItem("Database", "WARNING", "Nog geen database aanwezig."))
    try: settings.validate(settings.settings.as_dict()); items.append(HealthItem("Settings", "PASS", "Geldig"))
    except Exception as error: items.append(HealthItem("Settings", "FAIL", str(error)))
    diagnostics = settings.diagnostics()
    for name in ("FFmpeg", "ffprobe"):
        value = diagnostics[name]; items.append(HealthItem(name, "WARNING" if "niet gevonden" in value else "PASS", value))
    for name, section, key in (("Spotify", "spotify", "client_id"), ("YouTube", "youtube", "api_key")):
        configured = bool(settings.section(section)[key]); items.append(HealthItem(name, "PASS" if configured else "WARNING", "Geconfigureerd" if configured else "Niet geconfigureerd"))
    output = Path(settings.section("download")["output_root"])
    parent = next((p for p in (output, *output.parents) if p.exists()), Path.cwd())
    items.append(HealthItem("Schrijfrechten", "PASS" if os.access(parent, os.W_OK) else "FAIL", str(parent)))
    free = shutil.disk_usage(parent).free
    items.append(HealthItem("Vrije schijfruimte", "PASS" if free >= 1_000_000_000 else "WARNING", f"{free / 1_000_000_000:.1f} GB"))
    return HealthReport(tuple(items))


def run_self_test(database_path=DATABASE_BESTAND, settings_manager=None):
    health = run_health_check(database_path, settings_manager)
    checks = (
        ("Queue", "core.download_queue", "DownloadQueueManager"),
        ("Audio", "core.audio.processor", "AudioProcessor"),
        ("Metadata", "core.metadata.finalizer", "MetadataFinalizer"),
        ("Logging", "core.reliability.logging", "LoggingManager"),
    )
    extra = []
    for name, module_name, attribute in checks:
        try:
            module = __import__(module_name, fromlist=[attribute])
            getattr(module, attribute)
        except Exception as error:
            extra.append(HealthItem(name, "FAIL", str(error)))
        else:
            extra.append(HealthItem(name, "PASS", f"{attribute} beschikbaar"))
    return HealthReport(health.items + tuple(extra))
