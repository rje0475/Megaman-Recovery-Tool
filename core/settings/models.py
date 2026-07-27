"""Getypeerde snapshot van alle applicatie-instellingen."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsSnapshot:
    version: int
    general: dict
    spotify: dict
    youtube: dict
    download: dict
    audio: dict
    metadata: dict

    @classmethod
    def from_dict(cls, value):
        return cls(value["version"], *(dict(value[name]) for name in
                   ("general", "spotify", "youtube", "download", "audio", "metadata")))

    def as_dict(self):
        return {"version": self.version, "general": dict(self.general),
                "spotify": dict(self.spotify), "youtube": dict(self.youtube),
                "download": dict(self.download), "audio": dict(self.audio),
                "metadata": dict(self.metadata)}
