"""Is the computer locked, and how long since the last keyboard/mouse input?

Only these two values leave the machine. No keystrokes, windows or app usage are read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ActivitySample:
    locked: bool
    idle_seconds: int


class ActivityProbe(Protocol):
    def sample(self) -> ActivitySample: ...


class FakeActivity:
    """Test double: set `locked` / `idle_seconds` directly."""

    def __init__(self, locked: bool = False, idle_seconds: int = 0):
        self.locked = locked
        self.idle_seconds = idle_seconds

    def sample(self) -> ActivitySample:
        return ActivitySample(self.locked, self.idle_seconds)


class NullActivity:
    """Used off Windows (development): always unlocked and active."""

    def sample(self) -> ActivitySample:
        return ActivitySample(False, 0)


class WindowsActivity:
    """ctypes implementation.

    Idle: GetLastInputInfo gives the tick count (32-bit, ms) of the last input in this
    session; idle = (GetTickCount64 truncated to 32 bits) - dwTime, modulo 2**32 so the
    49-day wrap is harmless.

    Locked: we use WTSQuerySessionInformation(WTSSessionInfoEx) and read
    WTSINFOEX_LEVEL1.SessionFlags (WTS_SESSIONSTATE_LOCK = 0, UNLOCK = 1; correct on
    Windows 8+). It is chosen over OpenInputDesktop/SwitchDesktop because that check
    also reports "locked" during UAC prompts and depends on the calling thread's desktop
    and access rights, while the WTS query asks the session manager directly. If the WTS
    call fails or returns UNKNOWN (-1) we fall back to the OpenInputDesktop heuristic.
    """

    WTS_CURRENT_SERVER_HANDLE = 0
    WTS_CURRENT_SESSION = 0xFFFFFFFF
    WTS_SESSION_INFO_EX = 25
    WTS_SESSIONSTATE_LOCK = 0
    WTS_SESSIONSTATE_UNLOCK = 1
    DESKTOP_SWITCHDESKTOP = 0x0100

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._wt = wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._wtsapi = ctypes.WinDLL("wtsapi32", use_last_error=True)

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        # Only the leading fields of WTSINFOEXW (Level + WTSINFOEX_LEVEL1_W) that we need.
        class WTSINFOEX_HEAD(ctypes.Structure):
            _fields_ = [
                ("Level", wintypes.DWORD),
                ("SessionId", wintypes.ULONG),
                ("SessionState", ctypes.c_int),
                ("SessionFlags", wintypes.LONG),
            ]

        self._LASTINPUTINFO = LASTINPUTINFO
        self._HEAD = WTSINFOEX_HEAD
        self._kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        self._user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        self._user32.OpenInputDesktop.restype = wintypes.HANDLE
        self._user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        self._wtsapi.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._wtsapi.WTSFreeMemory.argtypes = [ctypes.c_void_p]

    def idle_seconds(self) -> int:
        info = self._LASTINPUTINFO()
        info.cbSize = self._ct.sizeof(info)
        if not self._user32.GetLastInputInfo(self._ct.byref(info)):
            return 0
        now32 = self._kernel32.GetTickCount64() & 0xFFFFFFFF
        return ((now32 - info.dwTime) & 0xFFFFFFFF) // 1000

    def _locked_wts(self) -> bool | None:
        buf = self._ct.c_void_p()
        size = self._wt.DWORD()
        ok = self._wtsapi.WTSQuerySessionInformationW(
            self.WTS_CURRENT_SERVER_HANDLE,
            self.WTS_CURRENT_SESSION,
            self.WTS_SESSION_INFO_EX,
            self._ct.byref(buf),
            self._ct.byref(size),
        )
        if not ok or not buf.value:
            return None
        try:
            head = self._ct.cast(buf, self._ct.POINTER(self._HEAD)).contents
            if head.Level != 1:
                return None
            if head.SessionFlags == self.WTS_SESSIONSTATE_LOCK:
                return True
            if head.SessionFlags == self.WTS_SESSIONSTATE_UNLOCK:
                return False
            return None
        finally:
            self._wtsapi.WTSFreeMemory(buf)

    def _locked_desktop(self) -> bool:
        # Fallback: when the workstation is locked the input desktop is Winlogon's and
        # we cannot open it with DESKTOP_SWITCHDESKTOP.
        h = self._user32.OpenInputDesktop(0, False, self.DESKTOP_SWITCHDESKTOP)
        if not h:
            return True
        self._user32.CloseDesktop(h)
        return False

    def locked(self) -> bool:
        res = self._locked_wts()
        return self._locked_desktop() if res is None else res

    def sample(self) -> ActivitySample:
        return ActivitySample(self.locked(), self.idle_seconds())


def default_activity() -> ActivityProbe:
    return WindowsActivity() if os.name == "nt" else NullActivity()
