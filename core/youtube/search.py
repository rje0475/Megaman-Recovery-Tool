"""Querystrategie, opslag en selectie voor YouTube-kandidaten."""

import json
import re
from datetime import datetime

from core.youtube.models import YouTubeCandidate
from core.youtube.provider import YouTubeSearchProvider
from core.youtube.scoring import NOISE, normaliseer_zoektekst, score_video


def bouw_zoekopdrachten(artist, title, version=None, original_filename=None):
    def clean(value):
        value = NOISE.sub(" ", str(value or ""))
        value = re.sub(r"\.(mp3|flac|wav|m4a)$", "", value, flags=re.I)
        value = re.sub(r"^\s*\d{8}\s+", "", value)
        return re.sub(r"\s+", " ", value).strip(" -_")
    queries = (
        clean(f"{artist} {title} {version or ''}"),
        clean(f"{artist} {title}"),
        clean(original_filename),
        clean(f"{title} {artist} audio"),
    )
    return tuple(dict.fromkeys(query for query in queries if query))


def zoek_youtube_kandidaten(
    database, recovery_item_id, provider=None, search_again=False
):
    from core.settings import get_settings_manager
    youtube_settings = get_settings_manager().section("youtube")
    provider = provider or YouTubeSearchProvider.from_environment()
    row = database.verbinding.execute(
        "SELECT * FROM recovery_items WHERE id=?", (int(recovery_item_id),)
    ).fetchone()
    if row is None:
        raise ValueError("Recovery-item bestaat niet.")
    original = row["verwacht_rel_pad"] or ""
    artist = row["bepaalde_artiest"] or ""
    title = row["bepaalde_titel"] or ""
    version = ""
    expected_duration = (
        round(row["selected_spotify_duration_ms"] / 1000)
        if row["selected_spotify_duration_ms"] else None
    )
    old_selected = row["selected_youtube_video_id"]
    queries = bouw_zoekopdrachten(artist, title, version, original)
    found = {}
    try:
        for query in queries:
            for video in provider.search(
                query, limit=getattr(provider, "default_limit", 10)
            ):
                score = score_video(
                    artist, title, version, expected_duration, video,
                    bool(youtube_settings["prefer_official_channels"]),
                )
                existing = found.get(video.video_id)
                if existing is None or score.confidence > existing.score.confidence:
                    found[video.video_id] = YouTubeCandidate(None, video, score)
    except Exception as error:
        database.verbinding.execute(
            "UPDATE recovery_items SET youtube_search_error=?, youtube_last_searched=? WHERE id=?",
            (str(error), datetime.now().isoformat(timespec="seconds"), row["id"]),
        )
        database.verbinding.commit()
        raise
    ranked = sorted(found.values(), key=lambda c: c.score.confidence, reverse=True)[
        :int(youtube_settings["max_candidates"])
    ]
    now = datetime.now().isoformat(timespec="seconds")
    for rank, candidate in enumerate(ranked, 1):
        v, s = candidate.video, candidate.score
        database.verbinding.execute(
            """
            INSERT INTO youtube_candidates (
              recovery_item_id, video_id, youtube_url, title, channel_name,
              duration_seconds, published_at, view_count, thumbnail_url,
              confidence, artist_score, title_score, version_score,
              duration_score, channel_score, penalty_score, warnings_json,
              raw_metadata_json, search_query, rank_number, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(recovery_item_id, video_id) DO UPDATE SET
              youtube_url=excluded.youtube_url, title=excluded.title,
              channel_name=excluded.channel_name, duration_seconds=excluded.duration_seconds,
              published_at=excluded.published_at, view_count=excluded.view_count,
              thumbnail_url=excluded.thumbnail_url, confidence=excluded.confidence,
              artist_score=excluded.artist_score, title_score=excluded.title_score,
              version_score=excluded.version_score, duration_score=excluded.duration_score,
              channel_score=excluded.channel_score, penalty_score=excluded.penalty_score,
              warnings_json=excluded.warnings_json, raw_metadata_json=excluded.raw_metadata_json,
              search_query=excluded.search_query, rank_number=excluded.rank_number,
              updated_at=excluded.updated_at
            """,
            (row["id"], v.video_id, v.url, v.title, v.channel_name,
             v.duration_seconds, v.published_at, v.view_count, v.thumbnail_url,
             s.confidence, s.artist_score, s.title_score, s.version_score,
             s.duration_score, s.channel_score, s.penalty_score,
             json.dumps(s.warnings, ensure_ascii=False),
             json.dumps(v.__dict__, ensure_ascii=False), queries[0] if queries else "",
             rank, now, now),
        )
    database.verbinding.execute(
        """UPDATE recovery_items SET youtube_last_searched=?,
        youtube_search_error=NULL, youtube_search_queries_json=? WHERE id=?""",
        (now, json.dumps(queries, ensure_ascii=False), row["id"]),
    )
    database.verbinding.commit()
    return laad_youtube_kandidaten(database, row["id"]), bool(
        old_selected and old_selected not in found
    )


def laad_youtube_kandidaten(database, recovery_item_id):
    rows = database.verbinding.execute(
        "SELECT * FROM youtube_candidates WHERE recovery_item_id=? ORDER BY confidence DESC, rank_number, id",
        (int(recovery_item_id),),
    ).fetchall()
    return tuple(rows)


def selecteer_youtube_kandidaat(database, recovery_item_id, candidate_id):
    row = database.verbinding.execute(
        "SELECT * FROM youtube_candidates WHERE id=? AND recovery_item_id=?",
        (int(candidate_id), int(recovery_item_id)),
    ).fetchone()
    if row is None:
        raise ValueError("YouTube-kandidaat hoort niet bij dit recovery-item.")
    now = datetime.now().isoformat(timespec="seconds")
    database.verbinding.execute(
        """UPDATE recovery_items SET selected_youtube_candidate_id=?,
        selected_youtube_video_id=?, selected_youtube_url=?, selected_youtube_title=?,
        selected_youtube_channel=?, selected_youtube_duration_seconds=?,
        selected_youtube_confidence=?, youtube_review_status='SELECTED',
        youtube_reviewed_at=?, preferred_audio_source='YOUTUBE' WHERE id=?""",
        (row["id"], row["video_id"], row["youtube_url"], row["title"],
         row["channel_name"], row["duration_seconds"], row["confidence"], now,
         int(recovery_item_id)),
    )
    database.verbinding.commit()


def markeer_geen_youtube_bron(database, recovery_item_id):
    database.verbinding.execute(
        """UPDATE recovery_items SET selected_youtube_candidate_id=NULL,
        selected_youtube_video_id=NULL, selected_youtube_url=NULL,
        youtube_review_status='REVIEWED_NONE', youtube_reviewed_at=?,
        preferred_audio_source=NULL WHERE id=?""",
        (datetime.now().isoformat(timespec="seconds"), int(recovery_item_id)),
    )
    database.verbinding.commit()
