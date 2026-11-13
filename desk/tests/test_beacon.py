import base64
import hashlib
import hmac

import pytest

from attendify_desk.beacon import (
    APPLE_COMPANY_ID,
    build_ibeacon_payload,
    full_manufacturer_data,
    rotating_ids,
    window_for,
)

UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"

# Rotating identity test vectors (also in desk/README.md).
VECTOR_SECRET = bytes(range(32))  # base64: AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=
VECTORS = {0: (40716, 55737), 1: (50226, 57433), 5_000_000: (9808, 3354)}


def parse_like_mobile(b64: str):
    """Port of parseIBeacon() from mobile/lib/beacons.ts."""
    b = list(base64.b64decode(b64))
    if len(b) < 25 or b[0] != 0x4C or b[1] != 0x00 or b[2] != 0x02 or b[3] != 0x15:
        return None
    h = bytes(b[4:20]).hex()
    uuid = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    return {"uuid": uuid, "major": (b[20] << 8) | b[21], "minor": (b[22] << 8) | b[23]}


def test_payload_layout():
    p = build_ibeacon_payload(UUID, 0x0102, 0xA0B0)
    assert len(p) == 23
    assert p[:2] == b"\x02\x15"
    assert p[2:18] == bytes.fromhex(UUID.replace("-", ""))
    assert p[18:20] == b"\x01\x02"
    assert p[20:22] == b"\xa0\xb0"
    assert p[22] == 0xC5  # -59 as int8


def test_known_vector():
    p = build_ibeacon_payload("e2c56db5-dffb-48d2-b060-d0f5a71096e0", 1, 2, -59)
    assert p.hex() == "0215e2c56db5dffb48d2b060d0f5a71096e000010002c5"


def test_company_id_little_endian():
    assert full_manufacturer_data(b"\x02")[:2] == b"\x4c\x00"
    assert APPLE_COMPANY_ID == 0x004C


@pytest.mark.parametrize("major,minor", [(1, 2), (0, 0), (65535, 65535), (0xABCD, 0x1234)])
def test_round_trip_with_mobile_parser(major, minor):
    full = full_manufacturer_data(build_ibeacon_payload(UUID, major, minor))
    parsed = parse_like_mobile(base64.b64encode(full).decode())
    assert parsed == {"uuid": UUID, "major": major, "minor": minor}


@pytest.mark.parametrize("major,minor,power", [(-1, 0, -59), (0, 65536, -59), (1, 1, 200)])
def test_rejects_out_of_range(major, minor, power):
    with pytest.raises(ValueError):
        build_ibeacon_payload(UUID, major, minor, power)


@pytest.mark.parametrize("window", sorted(VECTORS))
def test_rotating_ids_known_vectors(window):
    assert rotating_ids(VECTOR_SECRET, window) == VECTORS[window]


@pytest.mark.parametrize("window", [0, 1, 5_000_000])
def test_rotating_ids_follow_the_spec_formula(window):
    # Independent computation straight from the spec, not via rotating_ids.
    h = hmac.new(VECTOR_SECRET, window.to_bytes(8, "big"), hashlib.sha256).digest()
    assert rotating_ids(VECTOR_SECRET, window) == (h[0] << 8 | h[1], h[2] << 8 | h[3])


def test_rotating_ids_depend_on_secret_and_window():
    other = bytes(31) + b"\x01"
    assert rotating_ids(other, 1) != rotating_ids(VECTOR_SECRET, 1)
    assert rotating_ids(VECTOR_SECRET, 1) != rotating_ids(VECTOR_SECRET, 2)


def test_rotating_ids_rejects_negative_window():
    with pytest.raises(ValueError):
        rotating_ids(VECTOR_SECRET, -1)


def test_window_boundaries():
    assert window_for(0) == 0
    assert window_for(299.999) == 0
    assert window_for(300) == 1
    assert window_for(1_800_000_000) == 6_000_000
