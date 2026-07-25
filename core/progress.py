"""Backend-neutraal model voor gestructureerde live voortgang."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowProgress:
    stage: str
    current: int = 0
    total: int = 0
    percent: int = 0
    message: str = ""
    ok_count: int | None = None
    ffmpeg_error_count: int | None = None
    zero_byte_count: int | None = None
    matched_count: int | None = None
    low_confidence_count: int | None = None
    manual_review_count: int | None = None
    not_found_count: int | None = None
    error_count: int | None = None


def maak_progress(stage, current=0, total=0, message="", **counts):
    percent = (
        100 if total == 0 and current > 0
        else round(100 * current / total) if total else 0
    )
    return WorkflowProgress(
        stage=stage,
        current=current,
        total=total,
        percent=max(0, min(100, percent)),
        message=message,
        **counts,
    )
