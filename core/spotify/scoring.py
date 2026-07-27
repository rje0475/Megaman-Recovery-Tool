import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from core.spotify.models import SpotifyTrack


PRIMARY_ARTIST_MINIMUM = 0.72
ARTIST_ALIASES = {
    "30 seconds to mars": "thirty seconds to mars",
    "thirty seconds to mars": "thirty seconds to mars",
}

_SAMENWERKING = re.compile(
    r"(?i)(?:\bfeat(?:uring)?\.?\b|\bft\.?\b|\bvs\.?\b|"
    r"\band\b|&|\bx\b)"
)
_VERSIES = re.compile(
    r"(?i)\b(?:radio edit|single edit|album version|extended mix|"
    r"club mix|original mix|radio mix|extended|remix|mix|edit|version|"
    r"live|instrumental|explicit|clean)\b"
)


@dataclass(frozen=True)
class SpotifyScore:
    total: float
    title: float
    primary_artist: float
    extra_artists: float
    duration: float
    normalization: float
    rejected: bool = False
    rejection_reason: str | None = None


def _zonder_accenten(waarde):
    tekst = unicodedata.normalize("NFKD", str(waarde or ""))
    return "".join(
        teken for teken in tekst if not unicodedata.combining(teken)
    )


def normaliseer_tekst(waarde):
    tekst = _zonder_accenten(waarde)
    tekst = _SAMENWERKING.sub(" ", tekst)
    tekst = _VERSIES.sub(" ", tekst)
    return re.sub(r"[^a-z0-9]+", " ", tekst.casefold()).strip()


def normaliseer_artiest(waarde):
    tekst = normaliseer_tekst(waarde)
    return ARTIST_ALIASES.get(tekst, tekst)


def overeenkomst(links, rechts, normaliseerder=normaliseer_tekst):
    links = normaliseerder(links)
    rechts = normaliseerder(rechts)
    if not links or not rechts:
        return 0.0
    return SequenceMatcher(None, links, rechts).ratio()


def _splits_artiesten(artiest):
    delen = [
        deel.strip()
        for deel in _SAMENWERKING.split(str(artiest or ""))
        if deel.strip()
    ]
    return (delen[0] if delen else "", tuple(delen[1:]))


def _heeft_token_overlap(links, rechts):
    links_norm = normaliseer_artiest(links)
    rechts_norm = normaliseer_artiest(rechts)
    if links_norm.replace(" ", "") == rechts_norm.replace(" ", ""):
        return True
    links_tokens = set(links_norm.split())
    rechts_tokens = set(rechts_norm.split())
    return bool(links_tokens & rechts_tokens)


def _artiest_overeenkomst(links, rechts):
    links_norm = normaliseer_artiest(links)
    rechts_norm = normaliseer_artiest(rechts)
    if links_norm.replace(" ", "") == rechts_norm.replace(" ", ""):
        return 1.0
    return overeenkomst(links_norm, rechts_norm)


def _extra_artiest_score(lokale_extras, spotify_artiesten):
    if not lokale_extras:
        return 1.0
    kandidaten = tuple(spotify_artiesten[1:])
    if not kandidaten:
        return 0.0
    return sum(
        max(
            _artiest_overeenkomst(extra, kandidaat)
            for kandidaat in kandidaten
        )
        for extra in lokale_extras
    ) / len(lokale_extras)


def _duur_score(lokale_duur, spotify_duur):
    if not lokale_duur or not spotify_duur:
        return 1.0
    verschil = abs(int(lokale_duur) - int(spotify_duur))
    return max(0.0, 1.0 - verschil / 30000)


def bereken_score(artiest, titel, duur_ms, track: SpotifyTrack):
    primaire_artiest, extra_artiesten = _splits_artiesten(artiest)
    spotify_primair = track.artists[0] if track.artists else ""
    artiest_score = _artiest_overeenkomst(
        primaire_artiest, spotify_primair
    )
    artiest_afgewezen = (
        artiest_score < PRIMARY_ARTIST_MINIMUM
        or not _heeft_token_overlap(primaire_artiest, spotify_primair)
    )
    titel_score = overeenkomst(titel, track.title)
    extras_score = _extra_artiest_score(extra_artiesten, track.artists)
    duur_score = _duur_score(duur_ms, track.duration_ms)
    normalisatie_score = overeenkomst(
        f"{normaliseer_artiest(primaire_artiest)} {normaliseer_tekst(titel)}",
        f"{normaliseer_artiest(spotify_primair)} "
        f"{normaliseer_tekst(track.title)}",
    )
    totaal = (
        artiest_score * 0.55
        + titel_score * 0.25
        + extras_score * 0.08
        + duur_score * 0.07
        + normalisatie_score * 0.05
    )
    reden = None
    if artiest_afgewezen:
        reden = (
            "primaire artiest komt onvoldoende overeen "
            f"({artiest_score:.0%})"
        )
        totaal = 0.0
    return SpotifyScore(
        total=totaal,
        title=titel_score,
        primary_artist=artiest_score,
        extra_artists=extras_score,
        duration=duur_score,
        normalization=normalisatie_score,
        rejected=artiest_afgewezen,
        rejection_reason=reden,
    )


def score_track(artiest, titel, duur_ms, track: SpotifyTrack):
    return bereken_score(artiest, titel, duur_ms, track).total
