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
    write_genre: bool = True
    write_artwork: bool = True
    write_comments: bool = True
    write_track_number: bool = True
    write_year: bool = True

    @classmethod
    def from_environment(cls, environment=None):
        from core.settings import SettingsManager, get_settings_manager
        manager = (get_settings_manager() if environment is None else
                   SettingsManager(path=Path(os.devnull), environment=environment, create=False))
        metadata = manager.section("metadata")
        download = manager.section("download")
        return cls(
            output_root=Path(download["output_root"]),
            filename_template=metadata["filename_template"],
            folder_template=metadata["folder_template"],
            collision_policy=metadata["collision_policy"].title(),
            max_path_length=int(metadata["max_path_length"]),
            artwork_cache=Path(metadata["artwork_cache"]),
            write_genre=bool(metadata["write_genre"]),
            write_artwork=bool(metadata["write_artwork"]),
            write_comments=bool(metadata["write_comments"]),
            write_track_number=bool(metadata["write_track_number"]),
            write_year=bool(metadata["write_year"]),
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
