# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: single windowed exe with version info. Build from desk/:
#   uv run --group build pyinstaller attendify_desk.spec --noconfirm
import os
import sys

sys.path.insert(0, os.path.abspath("."))
from attendify_desk.icon import make_icon  # noqa: E402

os.makedirs("build", exist_ok=True)
ICON = os.path.abspath("build/attendify.ico")
make_icon("green", 256).save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

a = Analysis(
    ["run_desk.py"],
    pathex=["."],
    hiddenimports=[
        "winrt.windows.devices.bluetooth",
        "winrt.windows.devices.bluetooth.advertisement",
        "winrt.windows.devices.radios",
        "winrt.windows.storage.streams",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "pystray._win32",
    ],
    excludes=["pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AttendifyDesk",
    console=False,
    icon=ICON,
    version="version_info.txt",
    upx=False,
)
