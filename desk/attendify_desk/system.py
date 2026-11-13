"""Windows integration: single-instance mutex, start-with-Windows, log folders.

Everything degrades to harmless no-ops off Windows so the app can be imported in tests.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

from . import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTEX_NAME = "Local\\AttendifyDeskSingleInstance"
ERROR_ALREADY_EXISTS = 183

_mutex_handle = None  # keep a reference for the process lifetime


def acquire_single_instance() -> bool:
    """True if we are the only instance (named mutex, per login session)."""
    global _mutex_handle
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    _mutex_handle = handle
    return True


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable).with_name("pythonw.exe")
    return f'"{exe if exe.exists() else sys.executable}" -m attendify_desk'


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME / "logs"


class _RedactTokens(logging.Filter):
    """Belt and braces: never let a device token or identity secret reach the log file."""

    _token = re.compile(r"adt_[A-Za-z0-9_\-]+")
    # "secret": "<base64>", secret=<base64>, or a bare 32-byte base64 value after "secret"
    _secret = re.compile(r"""(?i)(secret["']?\s*[:=]\s*["']?)[A-Za-z0-9+/]{20,}={0,2}""")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = self._token.sub("adt_***", str(record.getMessage()))
        record.msg = self._secret.sub(r"\1***", msg)
        record.args = None
        return True


def setup_logging(directory: Path | None = None) -> Path:
    d = directory or log_dir()
    d.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        d / "attendify-desk.log", maxBytes=512_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactTokens())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return d
