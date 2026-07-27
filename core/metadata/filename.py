"""Veilige, configureerbare Windows-bestands- en mapnamen."""

import re
from pathlib import Path

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SPACES = re.compile(r"\s+")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


def safe_component(value, fallback="Onbekend"):
    value = SPACES.sub(" ", INVALID.sub("", str(value or ""))).strip(" .")
    if not value:
        value = fallback
    if value.partition(".")[0].upper() in RESERVED:
        value = f"_{value}"
    return value


def template_values(metadata):
    return {
        "artist": safe_component(metadata.artist, "Onbekende artiest"),
        "title": safe_component(metadata.title, "Onbekende titel"),
        "track": safe_component(metadata.track or "", "00"),
        "year": safe_component(metadata.year or "", "Onbekend jaar"),
        "week": safe_component(metadata.week or "", "00"),
    }


def render_filename(template, metadata):
    values = template_values(metadata)
    rendered = template.format_map(values)
    stem = safe_component(Path(rendered).stem)
    return f"{stem}.mp3"


def render_folder(template, metadata):
    values = template_values(metadata)
    parts = re.split(r"[/\\]+", template.format_map(values))
    return Path(*(safe_component(part) for part in parts if part))


def limit_path(filename, directory, maximum=240):
    available = max(16, maximum - len(str(Path(directory).resolve())) - 1 - 4)
    stem = Path(filename).stem[:available].rstrip(" .") or "audio"
    return f"{stem}.mp3"


def collision_target(path, policy):
    path = Path(path)
    if not path.exists() or policy.casefold() == "overwrite":
        return path
    if policy.casefold() == "skip":
        return None
    for number in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("Geen vrije bestandsnaam voor het eindbestand gevonden.")
