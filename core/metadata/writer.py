"""Mutagen ID3-writer; raakt de audiostream niet aan."""

from mutagen.id3 import APIC, COMM, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, ID3, ID3NoHeaderError

from .errors import MetadataWriteError


class MetadataWriter:
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
                (TRCK, metadata.track), (TPOS, metadata.disc),
                (TDRC, metadata.year), (TCON, metadata.genre),
            )
            for frame, value in values:
                if value:
                    tags.add(frame(encoding=3, text=str(value)))
            tags.add(COMM(encoding=3, lang="eng", desc="", text="Recovered by Megaman Recovery Tool"))
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Spotify album cover", data=artwork))
            tags.save(path, v2_version=3)
        except Exception as error:
            raise MetadataWriteError(f"ID3-metadata kon niet worden geschreven: {error}") from error

    def validate(self, path):
        try:
            tags = ID3(path)
        except Exception as error:
            raise MetadataWriteError(f"ID3-validatie mislukt: {error}") from error
        required = ("TIT2", "TPE1", "TALB", "TPE2", "COMM::eng")
        if any(not tags.get(key) for key in required) or not tags.getall("APIC"):
            raise MetadataWriteError("Niet alle vereiste ID3-tags of album-art zijn aanwezig.")
        return tuple(frame.FrameID for frame in tags.values())
