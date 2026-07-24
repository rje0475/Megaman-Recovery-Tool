import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


@dataclass(frozen=True)
class VergelijkItem:
    intern_pad: str
    status: str
    bestand: Path | None
    verwachte_grootte: int | None
    werkelijke_grootte: int | None
    reden: str


@dataclass(frozen=True)
class VergelijkResultaat:
    items: tuple[VergelijkItem, ...]
    extras: tuple[Path, ...]

    def aantal(self, status):
        return sum(item.status == status for item in self.items)


def _sleutel(pad):
    tekst = unicodedata.normalize("NFC", str(pad).replace("/", "\\"))
    return str(PureWindowsPath(tekst)).casefold()


def _leesbaar_mp3(pad):
    try:
        from mutagen import File
        audio = File(pad)
        return bool(audio and audio.info)
    except Exception:
        return False


def vergelijk_extractie(verwachte_items, extractiemap, mp3_lezer=None):
    extractiemap = Path(extractiemap)
    mp3_lezer = mp3_lezer or _leesbaar_mp3
    werkelijk = {
        _sleutel(p.relative_to(extractiemap)): p
        for p in extractiemap.rglob("*")
        if p.is_file() and p.suffix.casefold() == ".mp3"
    }
    gebruikt, items = set(), []
    for verwacht in verwachte_items:
        intern = verwacht["verwacht_rel_pad"]
        sleutel = _sleutel(intern)
        bestand = werkelijk.get(sleutel)
        grootte = verwacht.get("verwachte_grootte")
        if bestand is None:
            status, werkelijk_grootte, reden = "MISSING", None, "missing_after_salvage"
        else:
            gebruikt.add(sleutel)
            try:
                werkelijk_grootte = bestand.stat().st_size
                if werkelijk_grootte == 0:
                    status, reden = "ZERO_BYTE", "zero_byte_after_salvage"
                elif not mp3_lezer(bestand):
                    status, reden = "UNREADABLE", "unreadable_after_salvage"
                elif grootte is not None and werkelijk_grootte != grootte:
                    status, reden = "SIZE_MISMATCH", "corrupt_size_after_salvage"
                else:
                    status, reden = "OK", "ok"
            except OSError:
                status, werkelijk_grootte, reden = (
                    "UNREADABLE", None, "unreadable_after_salvage"
                )
        items.append(VergelijkItem(
            intern, status, bestand, grootte, werkelijk_grootte, reden
        ))
    extras = tuple(
        pad for sleutel, pad in werkelijk.items() if sleutel not in gebruikt
    )
    return VergelijkResultaat(tuple(items), extras)
