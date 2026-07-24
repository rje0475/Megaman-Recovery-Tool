import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath

from core.salvage_compare import VergelijkResultaat
from paden import normaliseer_relatief_pad_sleutel


@dataclass(frozen=True)
class DefinitieveClassificatie:
    vergelijking: VergelijkResultaat
    verwacht: int
    fysiek_aanwezig: int
    volledig_goed: int
    beschadigd_aanwezig: int
    nul_bytes: int
    onleesbaar: int
    ontbrekend: int
    ffmpeg_fouten_ingelezen: int
    duplicaten_verwijderd: int
    recovery_items: int


def _losse_sleutel(pad):
    tekst = unicodedata.normalize(
        "NFC", str(pad).strip().replace("/", "\\")
    )
    return str(PureWindowsPath(tekst)).casefold()


def _waarde(rij, naam, standaard=None):
    try:
        return rij[naam]
    except (KeyError, IndexError):
        return standaard


def _koppel_aan_verwacht(pad, verwachte_sleutels, wortels=()):
    kandidaten = []
    pad_obj = Path(str(pad))
    if pad_obj.is_absolute():
        for wortel in wortels:
            try:
                kandidaten.append(
                    normaliseer_relatief_pad_sleutel(
                        pad_obj.resolve().relative_to(Path(wortel).resolve())
                    )
                )
            except (OSError, ValueError):
                pass
    kandidaten.append(_losse_sleutel(pad))
    for kandidaat in kandidaten:
        if kandidaat in verwachte_sleutels:
            return kandidaat
        suffix_matches = tuple(
            sleutel for sleutel in verwachte_sleutels
            if kandidaat.endswith("\\" + sleutel)
        )
        if len(suffix_matches) == 1:
            return suffix_matches[0]
    return None


def classificeer_salvage_resultaat(
    vergelijking, analyse_rijen=(), wortels=()
):
    items_op_sleutel = {
        normaliseer_relatief_pad_sleutel(item.intern_pad): item
        for item in vergelijking.items
    }
    verwachte_sleutels = frozenset(items_op_sleutel)
    ffmpeg = {}
    expliciet_corrupt = set()
    nul_analyse = set()
    ffmpeg_ingelezen = 0
    for rij in analyse_rijen:
        paden = (rij["relatief_pad"], rij["bestand"])
        sleutel = next(
            (
                gekoppeld for pad in paden if pad
                if (
                    gekoppeld := _koppel_aan_verwacht(
                        pad, verwachte_sleutels, wortels
                    )
                )
            ),
            None,
        )
        if sleutel is None:
            continue
        if bool(_waarde(rij, "nul_bytes", False)):
            nul_analyse.add(sleutel)
        if _waarde(rij, "ffmpeg_status") == "ERROR":
            ffmpeg_ingelezen += 1
            details = ": ".join(
                str(waarde) for waarde in (
                    rij["ffmpeg_type"], rij["ffmpeg_melding"]
                ) if waarde
            ) or "Onbekende FFmpeg-fout"
            ffmpeg.setdefault(sleutel, details)
        if _waarde(rij, "rar_status") == "ERROR":
            expliciet_corrupt.add(sleutel)

    missing = {
        sleutel for sleutel, item in items_op_sleutel.items()
        if item.status == "MISSING"
    }
    zero_basis = {
        sleutel for sleutel, item in items_op_sleutel.items()
        if item.status == "ZERO_BYTE"
    }
    unreadable_basis = {
        sleutel for sleutel, item in items_op_sleutel.items()
        if item.status == "UNREADABLE"
    }
    zero = zero_basis | nul_analyse
    unreadable = unreadable_basis | set(ffmpeg) | expliciet_corrupt
    defect_sleutels = missing | zero | unreadable
    lidmaatschappen = (
        len(missing) + len(zero_basis) + len(nul_analyse)
        + len(unreadable_basis) + len(ffmpeg) + len(expliciet_corrupt)
    )

    definitieve_items = []
    for sleutel, item in items_op_sleutel.items():
        bronnen = {"salvage"}
        if sleutel in ffmpeg:
            bronnen.add("ffmpeg")
        if sleutel in nul_analyse:
            bronnen.add("bestandsscan")
        if sleutel in expliciet_corrupt:
            bronnen.add("rar_test")
        if sleutel in missing:
            nieuw = replace(
                item, ffmpeg_fout=ffmpeg.get(sleutel),
                bronnen=tuple(sorted(bronnen))
            )
        elif sleutel in zero:
            nieuw = replace(
                item, status="ZERO_BYTE",
                reden="zero_byte_after_salvage",
                ffmpeg_fout=ffmpeg.get(sleutel),
                bronnen=tuple(sorted(bronnen)),
            )
        elif sleutel in unreadable:
            nieuw = replace(
                item, status="UNREADABLE",
                reden=(
                    "ffmpeg_error_after_salvage"
                    if sleutel in ffmpeg
                    else "unreadable_after_salvage"
                ),
                ffmpeg_fout=ffmpeg.get(sleutel),
                bronnen=tuple(sorted(bronnen)),
            )
        else:
            nieuw = replace(item, bronnen=("salvage",))
        definitieve_items.append(nieuw)

    fysiek = sum(item.bestand is not None for item in definitieve_items)
    ontbrekend = sum(item.status == "MISSING" for item in definitieve_items)
    nul = sum(item.status == "ZERO_BYTE" for item in definitieve_items)
    onleesbaar = sum(item.status == "UNREADABLE" for item in definitieve_items)
    beschadigd = nul + onleesbaar
    volledig_goed = sum(
        item.status in ("OK", "SIZE_MISMATCH")
        for item in definitieve_items
    )
    return DefinitieveClassificatie(
        VergelijkResultaat(tuple(definitieve_items), vergelijking.extras),
        len(definitieve_items), fysiek, volledig_goed, beschadigd, nul,
        onleesbaar, ontbrekend, ffmpeg_ingelezen,
        max(0, lidmaatschappen - len(defect_sleutels)),
        len(defect_sleutels),
    )
