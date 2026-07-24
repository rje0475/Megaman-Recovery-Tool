from dataclasses import dataclass


MATCHED = "MATCHED"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NOT_FOUND = "NOT_FOUND"
MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True)
class SpotifyConfig:
    client_id: str
    client_secret: str
    market: str = "NL"
    timeout: int = 15


@dataclass(frozen=True)
class SpotifyTrack:
    track_id: str
    uri: str | None
    url: str | None
    album: str | None
    artists: tuple[str, ...]
    title: str
    duration_ms: int | None
    popularity: int | None


@dataclass(frozen=True)
class SpotifyMatch:
    track: SpotifyTrack | None
    confidence: float | None
    search_method: str | None
    status: str


@dataclass(frozen=True)
class SpotifySearchSummary:
    recovery_set_id: int
    archive_set_name: str
    total: int
    processed: int
    skipped: int
    matched: int
    low_confidence: int
    not_found: int
    manual_review: int
    skipped_automatic: int
    skipped_manual: int


@dataclass(frozen=True)
class RecoverySetInfo:
    recovery_set_id: int
    archive_set_name: str
    archive_name: str
    recovery_item_count: int
    created_at: str
    updated_at: str
