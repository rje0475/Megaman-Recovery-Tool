"""Mutagen ID3-writer; raakt de audiostream niet aan."""

from mutagen.id3 import APIC, COMM, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, ID3, ID3NoHeaderError

from .errors import MetadataWriteError


class MetadataWriter:
    def __init__(self, config=None):
        self.config = config

    def write(self, path, metadata, artwork):
        try:
            try:
                tags = ID3(path)
                tags.clear()
            except ID3NoHeaderError:
                tags = ID3()
            values = (
                (TIT2, metadata.title), (TPE1, metadata.artist),
                (TALB, metadata.album), (TPE2, metadata.album_artist),
                (TRCK, metadata.track if not self.config or self.config.write_track_number else None),
                (TPOS, metadata.disc if not self.config or self.config.write_track_number else None),
                (TDRC, metadata.year if not self.config or self.config.write_year else None),
                (TCON, metadata.genre if not self.config or self.config.write_genre else None),
            )
            for frame, value in values:
                if value:
                    tags.add(frame(encoding=3, text=str(value)))
            if not self.config or self.config.write_comments:
                tags.add(COMM(encoding=3, lang="eng", desc="", text="Recovered by Megaman Recovery Tool"))
            if not self.config or self.config.write_artwork:
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Spotify album cover", data=artwork))
            tags.save(path, v2_version=3)
        except Exception as error:
            raise MetadataWriteError(f"ID3-metadata kon niet worden geschreven: {error}") from error

    def validate(self, path):
        try:
            tags = ID3(path)
        except Exception as error:
            raise MetadataWriteError(f"ID3-validatie mislukt: {error}") from error
        required = ["TIT2", "TPE1", "TALB", "TPE2"]
        if not self.config or self.config.write_comments: required.append("COMM::eng")
        artwork_required = not self.config or self.config.write_artwork
        if any(not tags.get(key) for key in required) or (artwork_required and not tags.getall("APIC")):
            raise MetadataWriteError("Niet alle vereiste ID3-tags of album-art zijn aanwezig.")
        return tuple(frame.FrameID for frame in tags.values())
