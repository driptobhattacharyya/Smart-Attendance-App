"""iBeacon identity and advertisement payload (pure, cross-platform)."""

from __future__ import annotations

import uuid as uuidlib
from dataclasses import dataclass

APPLE_COMPANY_ID = 0x004C
DEFAULT_MEASURED_POWER = -59


@dataclass(frozen=True)
class BeaconIdentity:
    uuid: str  # lowercase, dashed
    major: int
    minor: int
    measured_power: int = DEFAULT_MEASURED_POWER

    @classmethod
    def from_json(cls, data: dict) -> BeaconIdentity:
        return cls(
            uuid=str(uuidlib.UUID(str(data["uuid"]))),
            major=int(data["major"]),
            minor=int(data["minor"]),
        )

    def to_json(self) -> dict:
        return {"uuid": self.uuid, "major": self.major, "minor": self.minor}


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
