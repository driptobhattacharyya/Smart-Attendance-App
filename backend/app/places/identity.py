"""Rotating identity of desk apps (docs/architecture-v5.md section 6, anti-cloning).

Window ``w = floor(unix_seconds / 300)``; ``h = HMAC-SHA256(secret, w as 8-byte big-endian)``;
``major = h[0] << 8 | h[1]``; ``minor = h[2] << 8 | h[3]``. The server accepts windows
``w-1 .. w+1`` around the reading's receive time. Test vectors are in the architecture appendix.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import TypeVar

WINDOW_SECONDS = 300
SECRET_BYTES = 32

K = TypeVar("K")


def window_for(unix_seconds: float) -> int:
    return int(unix_seconds // WINDOW_SECONDS)


def rotating_ids(secret: bytes, window: int) -> tuple[int, int]:
    digest = hmac.new(secret, window.to_bytes(8, "big"), hashlib.sha256).digest()
    return (digest[0] << 8) | digest[1], (digest[2] << 8) | digest[3]


def _unix(at: float | datetime) -> float:
    return at.timestamp() if isinstance(at, datetime) else float(at)


def match_reading(secret: bytes, major: int, minor: int, at: float | datetime) -> int | None:
    """The window (w-1, w or w+1 around ``at``) in which the reading matches, else None."""
    center = window_for(_unix(at))
    for window in (center, center - 1, center + 1):
        if window >= 0 and rotating_ids(secret, window) == (major, minor):
            return window
    return None


def match_devices(
    candidates: Iterable[tuple[K, bytes]], major: int, minor: int, at: float | datetime
) -> Sequence[K]:
    """Ids of every (id, secret) candidate the reading matches (16+16 bit ids can collide)."""
    return [
        key for key, secret in candidates if match_reading(secret, major, minor, at) is not None
    ]
