"""Datamodellen voor YouTube-zoeken; bevat bewust geen downloadmodel."""

from dataclasses import dataclass


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    url: str
    title: str
    channel_name: str
    duration_seconds: int | None = None
    published_at: str | None = None
    view_count: int | None = None
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class YouTubeScore:
    confidence: float
    artist_score: float
    title_score: float
    version_score: float
    duration_score: float
    channel_score: float
    penalty_score: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class YouTubeCandidate:
    id: int | None
    video: YouTubeVideo
    score: YouTubeScore
    selected: bool = False
