"""Niet-destructieve migratie van oudere settingsdocumenten."""

from copy import deepcopy

from .defaults import CURRENT_VERSION, DEFAULTS


def _merge(defaults, current):
    result = deepcopy(defaults)
    for key, value in (current or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def migrate(data):
    if not isinstance(data, dict):
        data = {}
    version = int(data.get("version", 0) or 0)
    # Versie 0 gebruikte enkele platte sleutels; behoud die waar aanwezig.
    if version == 0:
        aliases = {"ffmpeg_path": ("audio", "ffmpeg_path"),
                   "ffprobe_path": ("audio", "ffprobe_path"),
                   "output_root": ("download", "output_root"),
                   "filename_template": ("metadata", "filename_template")}
        for old, (section, key) in aliases.items():
            if old in data:
                data.setdefault(section, {})[key] = data[old]
    result = _merge(DEFAULTS, data)
    result["version"] = CURRENT_VERSION
    return result
