"""Modellen voor de metadata- en finalisatiepipeline."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetadataConfig:
    output_root: Path = Path("Recovered")
    filename_template: str = "{artist} - {title}.mp3"
    folder_template: str = "{year}/Week {week}"
    collision_policy: str = "Rename"
    max_path_length: int = 240
    artwork_cache: Path = Path("downloads/artwork_cache")

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        return cls(
            output_root=Path(env.get("OUTPUT_ROOT", "Recovered")),
            filename_template=env.get("FILENAME_TEMPLATE", "{artist} - {title}.mp3"),
            folder_template=env.get("FOLDER_TEMPLATE", "{year}/Week {week}"),
            collision_policy=env.get("COLLISION_POLICY", "Rename").title(),
            max_path_length=int(env.get("MAX_FINAL_PATH_LENGTH", "240")),
            artwork_cache=Path(env.get("ARTWORK_CACHE", "downloads/artwork_cache")),
        )


@dataclass(frozen=True)
class TrackMetadata:
    title: str
    artist: str
    album: str
    album_artist: str
    track: str | None = None
    disc: str | None = None
    year: str | None = None
    week: str | None = None
    genre: str | None = None
    cover_url: str | None = None


@dataclass(frozen=True)
class FinalizationResult:
    final_path: Path
    filename: str
    size: int
    metadata_written: bool
    artwork_written: bool
    tags: tuple[str, ...]
    source_removed: bool
