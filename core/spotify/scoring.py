import re
import unicodedata
from difflib import SequenceMatcher

from core.spotify.models import SpotifyTrack


def normaliseer_tekst(waarde):
    tekst = unicodedata.normalize("NFKD", str(waarde or ""))
    tekst = "".join(
        teken for teken in tekst if not unicodedata.combining(teken)
    )
    tekst = re.sub(r"(?i)\b(?:feat(?:uring)?|ft)\.?\b", " feat ", tekst)
    return re.sub(r"[^a-z0-9]+", " ", tekst.casefold()).strip()


def overeenkomst(links, rechts):
    links, rechts = normaliseer_tekst(links), normaliseer_tekst(rechts)
    if not links or not rechts:
        return 0.0
    return SequenceMatcher(None, links, rechts).ratio()


def score_track(artiest, titel, duur_ms, track: SpotifyTrack):
    artiest_score = max(
        (overeenkomst(artiest, kandidaat) for kandidaat in track.artists),
        default=0.0,
    )
    titel_score = overeenkomst(titel, track.title)
    if duur_ms and track.duration_ms:
        verschil = abs(int(duur_ms) - int(track.duration_ms))
        duur_score = max(0.0, 1.0 - verschil / 30000)
        return (
            artiest_score * 0.42
            + titel_score * 0.48
            + duur_score * 0.10
        )
    return artiest_score * 0.45 + titel_score * 0.55
