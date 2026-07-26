"""Transparante, conservatieve scoring van YouTube-videokandidaten."""

import re
import unicodedata
from difflib import SequenceMatcher

from core.recovery_review import herken_versies
from core.youtube.models import YouTubeScore


NOISE = re.compile(
    r"\b(official music video|official video|lyric video|lyrics|videoclip|"
    r"official audio|audio|hd|hq|4k)\b", re.I
)
NEGATIVE = {
    "cover": "Mogelijke cover", "tribute": "Tribute",
    "karaoke": "Karaoke", "nightcore": "Nightcore",
    "sped up": "Sped up", "slowed": "Slowed",
    "chipmunk": "Chipmunk", "reaction": "Reaction",
    "tutorial": "Tutorial",
}


def normaliseer_zoektekst(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = NOISE.sub(" ", value)
    value = re.sub(r"\.(mp3|flac|wav|m4a)$", "", value)
    value = re.sub(r"^\s*\d{8}\s+", "", value)
    value = re.sub(r"[^\w]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _similarity(a, b):
    a, b = normaliseer_zoektekst(a), normaliseer_zoektekst(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b)) * .2 + .8
    return SequenceMatcher(None, a, b).ratio()


def score_video(artist, title, version, expected_duration, video):
    candidate = normaliseer_zoektekst(video.title)
    artist_score = _similarity(artist, candidate)
    title_score = _similarity(title, candidate)
    wanted_versions = set(herken_versies(f"{title} {version or ''}"))
    found_versions = set(herken_versies(video.title))
    version_score = 1.0 if wanted_versions == found_versions else (
        .65 if wanted_versions & found_versions else .35
    )
    warnings = []
    for term, warning in NEGATIVE.items():
        if term in candidate:
            warnings.append(warning)
    for term, warning in (("live", "Mogelijke live-versie"),
                          ("remix", "Andere remix"),
                          ("instrumental", "Instrumental")):
        if term in candidate and not any(term in v.casefold() for v in wanted_versions):
            warnings.append(warning)
    duration_score = .5
    if expected_duration and video.duration_seconds:
        difference = abs(video.duration_seconds - expected_duration)
        duration_score = max(0.0, 1.0 - difference / max(30, expected_duration * .25))
        if difference > max(30, expected_duration * .2):
            warnings.append("Duur wijkt sterk af")
    elif not video.duration_seconds:
        warnings.append("Geen duur beschikbaar")
    channel = normaliseer_zoektekst(video.channel_name)
    channel_score = .5
    if "topic" in channel or "official" in channel or _similarity(artist, channel) >= .8:
        channel_score = 1.0
    penalty = min(.6, len(set(warnings)) * .10)
    total = (
        artist_score * .36 + title_score * .34 + version_score * .12
        + duration_score * .10 + channel_score * .08 - penalty
    )
    return YouTubeScore(
        confidence=max(0.0, min(1.0, total)), artist_score=artist_score,
        title_score=title_score, version_score=version_score,
        duration_score=duration_score, channel_score=channel_score,
        penalty_score=penalty, warnings=tuple(dict.fromkeys(warnings)),
    )
