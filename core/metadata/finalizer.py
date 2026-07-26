"""Veilige metadatafinalisatie van een reeds gevalideerde MP3."""

import shutil
import uuid
from pathlib import Path

from .artwork import ArtworkCache
from .errors import FinalizationSkipped, FinalizationValidationError, MetadataError, MetadataMissingError
from .filename import collision_target, limit_path, render_filename, render_folder
from .models import FinalizationResult, TrackMetadata
from .writer import MetadataWriter


class MetadataFinalizer:
    def __init__(self, database, config, writer=None, artwork_cache=None):
        self.database = database
        self.config = config
        self.writer = writer or MetadataWriter(config)
        self.artwork_cache = artwork_cache or ArtworkCache(config.artwork_cache)

    def resolve_metadata(self, recovery_item_id):
        row = self.database.verbinding.execute(
            """SELECT r.selected_spotify_artist,r.selected_spotify_title,
            r.selected_spotify_album,r.bepaald_tracknummer,r.verwacht_rel_pad,
            c.album_cover_url,c.release_date
            FROM recovery_items r LEFT JOIN spotify_candidates c
              ON c.id=r.selected_spotify_candidate_id WHERE r.id=?""",
            (recovery_item_id,),
        ).fetchone()
        if not row or not row["selected_spotify_artist"] or not row["selected_spotify_title"]:
            raise MetadataMissingError("Een opgeslagen Spotify-match met artiest en titel is vereist.")
        name = Path(row["verwacht_rel_pad"] or "").stem
        code = name.split(" ", 1)[0] if name else ""
        year = None
        if len(code) == 8 and code.isdigit():
            short_year = int(code[:2])
            year = str(1900 + short_year if short_year >= 70 else 2000 + short_year)
        week = code[2:4] if len(code) == 8 and code.isdigit() else None
        release = row["release_date"] or ""
        return TrackMetadata(
            title=row["selected_spotify_title"], artist=row["selected_spotify_artist"],
            album=row["selected_spotify_album"] or "Onbekend album",
            album_artist=row["selected_spotify_artist"], track=row["bepaald_tracknummer"],
            year=year or release[:4] or None, week=week,
            cover_url=row["album_cover_url"],
        )

    def finalize(self, job):
        source = Path(job.processed_path or "")
        if not source.is_file() or source.stat().st_size <= 0:
            raise FinalizationValidationError("Het verwerkte MP3-bestand ontbreekt of is leeg.")
        metadata = self.resolve_metadata(job.recovery_item_id)
        folder = Path(self.config.output_root) / render_folder(self.config.folder_template, metadata)
        folder.mkdir(parents=True, exist_ok=True)
        filename = limit_path(render_filename(self.config.filename_template, metadata), folder, self.config.max_path_length)
        target = collision_target(folder / filename, self.config.collision_policy)
        if target is None:
            raise FinalizationSkipped("Het doelbestand bestaat en het beleid is Skip.")
        staging = folder / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(source, staging)
            artwork = (self.artwork_cache.get(metadata.cover_url).read_bytes()
                       if self.config.write_artwork else b"")
            self.writer.write(staging, metadata, artwork)
            tags = self.writer.validate(staging)
            if staging.stat().st_size <= 0:
                raise FinalizationValidationError("Het gefinaliseerde bestand is leeg.")
            staging.replace(target)
            self.writer.validate(target)
            source.unlink()
            return FinalizationResult(target, target.name, target.stat().st_size,
                                      True, self.config.write_artwork, tags, True)
        except MetadataError:
            staging.unlink(missing_ok=True)
            raise
        except Exception as error:
            staging.unlink(missing_ok=True)
            raise FinalizationValidationError(
                f"Finalisatie kon niet veilig worden voltooid: {error}"
            ) from error
