"""iBeacon identity and advertisement payload (pure, cross-platform)."""

from __future__ import annotations

import hashlib
import hmac
import math
import uuid as uuidlib
from dataclasses import dataclass

APPLE_COMPANY_ID = 0x004C
DEFAULT_MEASURED_POWER = -59
ROTATION_SECONDS = 300  # the identity changes every 5 minutes


@dataclass(frozen=True)
class BeaconIdentity:
    uuid: str  # lowercase, dashed
    major: int
    minor: int
    measured_power: int = DEFAULT_MEASURED_POWER


def window_for(unix_seconds: float) -> int:
    """The rotation window (5-minute slot) that a unix timestamp falls in."""
    return math.floor(unix_seconds / ROTATION_SECONDS)


def rotating_ids(secret: bytes, window: int) -> tuple[int, int]:
    """(major, minor) for one window, per architecture-v5 section 6.

    h = HMAC-SHA256(secret, window as 8-byte big-endian); major = h[0..1], minor = h[2..3],
    both big-endian u16. The server computes the same values, so a fixed copy of a desk's
    (major, minor) is useless after at most one window.
    """
    if window < 0:
        raise ValueError("window must be non-negative")
    h = hmac.new(secret, window.to_bytes(8, "big"), hashlib.sha256).digest()
    return (h[0] << 8 | h[1], h[2] << 8 | h[3])


def build_ibeacon_payload(
    uuid: str, major: int, minor: int, measured_power: int = DEFAULT_MEASURED_POWER
) -> bytes:
    """Manufacturer-specific data *after* the company id.

    02 15 | UUID (16, big endian) | major (BE u16) | minor (BE u16) | measured power (i8)

    Windows takes the company id (0x004C) separately and writes it little-endian
    (4C 00) in front of this, giving the full frame the phone parser expects.
    """
    if not 0 <= major <= 0xFFFF or not 0 <= minor <= 0xFFFF:
        raise ValueError("major and minor must be 0..65535")
    if not -128 <= measured_power <= 127:
        raise ValueError("measured_power must fit in int8")
    return (
        bytes([0x02, 0x15])
        + uuidlib.UUID(uuid).bytes
        + major.to_bytes(2, "big")
        + minor.to_bytes(2, "big")
        + measured_power.to_bytes(1, "big", signed=True)
    )


def full_manufacturer_data(payload: bytes) -> bytes:
    """Company id (LE) + payload: what a scanner sees as manufacturer data."""
    return APPLE_COMPANY_ID.to_bytes(2, "little") + payload
