import base64

import pytest

from attendify_desk.beacon import (
    APPLE_COMPANY_ID,
    BeaconIdentity,
    build_ibeacon_payload,
    full_manufacturer_data,
)

UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"


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


def test_identity_from_json_normalises_uuid():
    b = BeaconIdentity.from_json({"uuid": UUID.upper(), "major": "3", "minor": 4})
    assert b == BeaconIdentity(UUID, 3, 4)
