import os
import shutil
from dataclasses import dataclass
from pathlib import Path


WINRAR_LOCATIES = (
    Path(r"C:\Program Files\WinRAR\WinRAR.exe"),
    Path(r"C:\Program Files (x86)\WinRAR\WinRAR.exe"),
)
SEVENZIP_LOCATIES = (
    Path(r"C:\Program Files\7-Zip\7z.exe"),
    Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
)


@dataclass(frozen=True)
class ToolResultaat:
    naam: str
    pad: Path | None
    beschikbaar: bool
    bron: str | None
    foutmelding: str | None = None


def detecteer_tool(
    naam, env_naam, standaardlocaties, path_namen,
    omgeving=None, which=shutil.which,
):
    omgeving = os.environ if omgeving is None else omgeving
    env_pad = omgeving.get(env_naam)
    kandidaten = (
        [(env_pad, "ENV")] if env_pad else []
    ) + [(pad, "STANDARD") for pad in standaardlocaties]
    for kandidaat, bron in kandidaten:
        pad = Path(str(kandidaat).strip().strip('"'))
        if pad.is_file():
            return ToolResultaat(naam, pad.resolve(), True, bron)
    for path_naam in path_namen:
        gevonden = which(path_naam)
        if gevonden and Path(gevonden).is_file():
            return ToolResultaat(
                naam, Path(gevonden).resolve(), True, "PATH"
            )
    return ToolResultaat(
        naam, None, False, None,
        f"{naam} niet gevonden; stel {env_naam} in.",
    )


def detecteer_winrar(**kwargs):
    return detecteer_tool(
        "WinRAR", "WINRAR_PATH", WINRAR_LOCATIES,
        ("WinRAR.exe", "winrar"), **kwargs,
    )


def detecteer_7zip(**kwargs):
    return detecteer_tool(
        "7-Zip", "SEVENZIP_PATH", SEVENZIP_LOCATIES,
        ("7z.exe", "7z", "7zz"), **kwargs,
    )
