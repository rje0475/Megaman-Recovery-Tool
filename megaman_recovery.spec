# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "README.md"), "."), (str(root / "LICENSE"), "."),
           (str(root / "CHANGELOG.md"), ".")],
    hiddenimports=["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
                   "mutagen", "yt_dlp"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    name="MegamanRecoveryTool", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=False, disable_windowed_traceback=False,
    exclude_binaries=True,
)
collect = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=True, name="MegamanRecoveryTool",
)
