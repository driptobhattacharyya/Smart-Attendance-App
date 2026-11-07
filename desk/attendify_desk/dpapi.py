"""Windows DPAPI (CryptProtectData) via ctypes. Current-user scope, no extra entropy."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))  # keep `buf` alive while blob is used
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


class DpapiProtector:
    """Flags=0 means the key is tied to the current Windows user (not the machine)."""

    def __init__(self) -> None:
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def _read_and_free(self, blob: _Blob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            self._kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))

    def protect(self, data: bytes) -> bytes:
        inp, _keep = _to_blob(data)
        out = _Blob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(inp), "Attendify Desk", None, None, None, 0, ctypes.byref(out)
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return self._read_and_free(out)

    def unprotect(self, data: bytes) -> bytes:
        inp, _keep = _to_blob(data)
        out = _Blob()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return self._read_and_free(out)
