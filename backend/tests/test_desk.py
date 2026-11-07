"""Contract v4: Attendify Desk (pairing, heartbeat, desk-aware presence)."""

import asyncio
import hashlib
import json
from datetime import timedelta

import pytest

from app.db import utcnow
from app.desk import CODE_ALPHABET
from app.presence import desk_block_reason, evaluate_signal, presence_reason

from .conftest import (
    INSIDE,
    OUTSIDE,
    auth,
    enroll,
    make_beacon,
    reading,
    register,
    verify,
)

DEFAULT_DESK_UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"


def make_desk(client, admin_token, office_id, **overrides):
    body = {"office_id": office_id, "name": "Desk PC", "kind": "desk_app", **overrides}
    r = client.post("/api/beacons", headers=auth(admin_token), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def pairing_code(client, admin_token, beacon_id) -> str:
    r = client.post(f"/api/beacons/{beacon_id}/pairing-code", headers=auth(admin_token))
    assert r.status_code == 200, r.text
    return r.json()["code"]


def pair(client, code, **overrides):
    body = {"code": code, "hostname": "PC-01", "os": "Windows 11", "agent_version": "1.0.0"}
    return client.post("/api/desk/pair", json={**body, **overrides})


def pair_desk(client, admin_token, beacon_id) -> str:
    r = pair(client, pairing_code(client, admin_token, beacon_id))
    assert r.status_code == 200, r.text
    return r.json()["device_token"]


def device(token: str) -> dict:
    return {"Authorization": f"Device {token}"}


def beat(client, token, locked=False, idle=0, advertising=True):
    return client.post(
        "/api/desk/heartbeat",
        headers=device(token),
        json={
            "locked": locked,
            "idle_seconds": idle,
            "advertising": advertising,
            "agent_version": "1.0.1",
        },
    )


def get_beacon(client, admin_token, beacon_id) -> dict:
    listed = client.get("/api/beacons", headers=auth(admin_token)).json()
    return next(b for b in listed if b["id"] == beacon_id)


def raw_beacon(db, beacon_id):
    from bson import ObjectId

    return asyncio.run(db.beacons.find_one({"_id": ObjectId(beacon_id)}))


# ---------- creation and allocation ----------


def test_desk_beacon_defaults(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    assert b["kind"] == "desk_app" and b["uuid"] == DEFAULT_DESK_UUID
    assert b["major"] == 1 and b["minor"] == 1
    assert b["device"] is None and b["paired"] is False and b["online"] is False


def test_tags_default_kind_and_need_uuid(client, admin_token, office_id):
    tag = make_beacon(client, admin_token, office_id)
    assert tag["kind"] == "tag" and tag["device"] is None and tag["paired"] is False
    r = client.post(
        "/api/beacons", headers=auth(admin_token), json={"office_id": office_id, "name": "x"}
    )
    assert r.status_code == 422


def test_allocation_is_unique_and_skips_taken_identities(client, admin_token, office_id):
    make_desk(client, admin_token, office_id, major=1, minor=2)  # taken by hand
    seen = set()
    for i in range(6):
        b = make_desk(client, admin_token, office_id, name=f"PC {i}")
        seen.add((b["major"], b["minor"]))
    assert len(seen) == 6 and (1, 2) not in seen
    # an explicit identity that is taken is refused
    r = client.post(
        "/api/beacons",
        headers=auth(admin_token),
        json={"office_id": office_id, "name": "dup", "kind": "desk_app", "major": 1, "minor": 2},
    )
    assert r.status_code == 409
    # a different UUID has its own number space
    other = make_desk(client, admin_token, office_id, uuid="11111111-2222-3333-4444-555555555555")
    assert (other["major"], other["minor"]) == (1, 1)


def test_allocation_rolls_over_minor(client, admin_token, office_id, db):
    asyncio.run(db.counters.insert_one({"_id": f"desk_beacon:{DEFAULT_DESK_UUID}", "seq": 65535}))
    b = make_desk(client, admin_token, office_id)
    assert (b["major"], b["minor"]) == (2, 1)


def test_desk_app_needs_major_and_minor(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    r = client.patch(f"/api/beacons/{b['id']}", headers=auth(admin_token), json={"minor": None})
    assert r.status_code == 422
    r = client.post(
        "/api/beacons",
        headers=auth(admin_token),
        json={"office_id": office_id, "name": "x", "kind": "desk_app", "major": 3},
    )
    assert r.status_code == 422


# ---------- pairing ----------


def test_pairing_happy_path(client, admin_token, office_id):
    register(client, office_id)
    b = make_desk(client, admin_token, office_id, assigned_employee_id="EMP0001")
    r = client.post(f"/api/beacons/{b['id']}/pairing-code", headers=auth(admin_token))
    body = r.json()
    code = body["code"]
    assert len(code) == 9 and code[4] == "-"
    assert set(code.replace("-", "")) <= set(CODE_ALPHABET)
    assert not set("0O1IL") & set(code)
    assert body["expires_at"]

    r = pair(client, code.lower().replace("-", " "))  # typed loosely
    assert r.status_code == 200, r.text
    paired = r.json()
    assert paired["device_token"].startswith("adt_")
    assert paired["beacon"] == {
        "id": b["id"],
        "name": "Desk PC",
        "uuid": DEFAULT_DESK_UUID,
        "major": b["major"],
        "minor": b["minor"],
    }
    assert paired["employee"] == {"employee_id": "EMP0001", "name": "Alice"}
    assert paired["heartbeat_interval_seconds"] == 60

    shown = get_beacon(client, admin_token, b["id"])
    assert shown["paired"] is True and shown["online"] is True
    assert shown["device"]["hostname"] == "PC-01" and shown["device"]["os"] == "Windows 11"
    assert shown["device"]["paired_at"]


def test_pair_without_assigned_employee(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    r = pair(client, pairing_code(client, admin_token, b["id"]))
    assert r.json()["employee"] is None


def test_invalid_used_and_expired_codes_share_one_message(client, admin_token, office_id, db):
    b = make_desk(client, admin_token, office_id)
    code = pairing_code(client, admin_token, b["id"])
    assert pair(client, code).status_code == 200
    used = pair(client, code)
    invalid = pair(client, "ZZZZ-ZZZZ")
    assert used.status_code == invalid.status_code == 404
    assert used.json() == invalid.json() == {"detail": "Invalid or expired code"}

    expired = pairing_code(client, admin_token, b["id"])
    asyncio.run(
        db.beacons.update_many(
            {"pairing": {"$ne": None}},
            {"$set": {"pairing.expires_at": utcnow() - timedelta(seconds=1)}},
        )
    )
    r = pair(client, expired)
    assert r.status_code == 404 and r.json() == {"detail": "Invalid or expired code"}


def test_new_code_invalidates_previous_code(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    first = pairing_code(client, admin_token, b["id"])
    second = pairing_code(client, admin_token, b["id"])
    assert pair(client, first).status_code == 404
    assert pair(client, second).status_code == 200


def test_repairing_replaces_old_token(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    old = pair_desk(client, admin_token, b["id"])
    assert beat(client, old).status_code == 200
    new = pair_desk(client, admin_token, b["id"])
    assert beat(client, old).status_code == 401
    assert beat(client, new).status_code == 200


def test_pairing_code_only_for_desk_apps_and_admins(client, admin_token, office_id):
    tag = make_beacon(client, admin_token, office_id)
    r = client.post(f"/api/beacons/{tag['id']}/pairing-code", headers=auth(admin_token))
    assert r.status_code == 409
    assert (
        client.post(f"/api/beacons/{tag['id']}/unpair", headers=auth(admin_token)).status_code
        == 409
    )
    missing = "0" * 24
    assert (
        client.post(f"/api/beacons/{missing}/pairing-code", headers=auth(admin_token)).status_code
        == 404
    )
    desk = make_desk(client, admin_token, office_id)
    emp = register(client, office_id)["access_token"]
    for path in ("pairing-code", "unpair"):
        assert (
            client.post(f"/api/beacons/{desk['id']}/{path}", headers=auth(emp)).status_code == 403
        )
        assert client.post(f"/api/beacons/{desk['id']}/{path}").status_code == 401


def test_unpair(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    token = pair_desk(client, admin_token, b["id"])
    outstanding = pairing_code(client, admin_token, b["id"])
    r = client.post(f"/api/beacons/{b['id']}/unpair", headers=auth(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["paired"] is False and body["online"] is False and body["device"] is None
    assert beat(client, token).status_code == 401
    assert pair(client, outstanding).status_code == 404


# ---------- heartbeat ----------


def test_heartbeat_updates_device_and_returns_identity(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    token = pair_desk(client, admin_token, b["id"])
    r = beat(client, token, locked=True, idle=42, advertising=False)
    assert r.status_code == 200
    assert r.json() == {
        "beacon": {"uuid": DEFAULT_DESK_UUID, "major": b["major"], "minor": b["minor"]},
        "heartbeat_interval_seconds": 60,
        "paired": True,
    }
    d = get_beacon(client, admin_token, b["id"])["device"]
    assert d["locked"] is True and d["idle_seconds"] == 42 and d["advertising"] is False
    assert d["agent_version"] == "1.0.1" and d["hostname"] == "PC-01"

    # an admin edit is picked up
    client.patch(f"/api/beacons/{b['id']}", headers=auth(admin_token), json={"major": 77})
    assert beat(client, token).json()["beacon"]["major"] == 77


def test_heartbeat_auth(client, admin_token, office_id):
    b = make_desk(client, admin_token, office_id)
    token = pair_desk(client, admin_token, b["id"])
    body = {"locked": False, "idle_seconds": 0, "advertising": True, "agent_version": "1"}
    url = "/api/desk/heartbeat"
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=device("adt_nope")).status_code == 401
    assert (
        client.post(url, json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )
    assert client.post(url, json=body, headers={"Authorization": "Device "}).status_code == 401
    # a user JWT is not a device token
    assert client.post(url, json=body, headers=auth(admin_token)).status_code == 401
    r = client.post(url, json={**body, "idle_seconds": -1}, headers=device(token))
    assert r.status_code == 422


def test_device_token_of_one_beacon_cannot_touch_another(client, admin_token, office_id):
    a = make_desk(client, admin_token, office_id, name="A")
    b = make_desk(client, admin_token, office_id, name="B")
    token_a = pair_desk(client, admin_token, a["id"])
    token_b = pair_desk(client, admin_token, b["id"])
    beat(client, token_a, locked=True, idle=500)
    beat(client, token_b, locked=False, idle=3)
    da = get_beacon(client, admin_token, a["id"])["device"]
    db_ = get_beacon(client, admin_token, b["id"])["device"]
    assert (da["locked"], da["idle_seconds"]) == (True, 500)
    assert (db_["locked"], db_["idle_seconds"]) == (False, 3)
    assert beat(client, token_a).json()["beacon"]["major"] == a["major"]
    assert beat(client, token_b).json()["beacon"]["major"] == b["major"]
    # unpairing A leaves B working
    client.post(f"/api/beacons/{a['id']}/unpair", headers=auth(admin_token))
    assert beat(client, token_a).status_code == 401
    assert beat(client, token_b).status_code == 200


def test_secrets_are_hashed_and_never_returned(client, admin_token, office_id, db):
    b = make_desk(client, admin_token, office_id)
    r = client.post(f"/api/beacons/{b['id']}/pairing-code", headers=auth(admin_token))
    code = r.json()["code"]
    paired = pair(client, code)
    token = paired.json()["device_token"]
    code2 = pairing_code(client, admin_token, b["id"])

    raw = raw_beacon(db, b["id"])
    assert raw["device_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert (
        raw["pairing"]["code_hash"] == hashlib.sha256(code2.replace("-", "").encode()).hexdigest()
    )
    assert token not in json.dumps(raw, default=str)
    assert code2 not in json.dumps(raw, default=str)

    emp = register(client, office_id)["access_token"]
    manager_view = [
        client.get("/api/beacons", headers=auth(admin_token)).text,
        client.get("/api/beacons/mine", headers=auth(emp)).text,
        client.post(f"/api/beacons/{b['id']}/unpair", headers=auth(admin_token)).text,
        client.patch(f"/api/beacons/{b['id']}", headers=auth(admin_token), json={"name": "n"}).text,
        paired.text.replace(token, ""),  # the token itself is only in the pair response
    ]
    for text in manager_view:
        for needle in ("device_token_hash", "code_hash", "pairing", raw["device_token_hash"]):
            assert needle not in text


# ---------- pure presence rules ----------


def _desk_doc(now, **device_fields):
    base = {"locked": False, "idle_seconds": 0, "last_seen_at": now}
    return {
        "kind": "desk_app",
        "uuid": DEFAULT_DESK_UUID,
        "major": 1,
        "minor": 1,
        "device_token_hash": "x",
        "device": {**base, **device_fields},
    }


def test_desk_block_reason():
    now = utcnow()
    assert desk_block_reason(_desk_doc(now), now, 180, 600) is None
    assert desk_block_reason(_desk_doc(now, locked=True), now, 180, 600) == "desk_locked"
    assert desk_block_reason(_desk_doc(now, idle_seconds=601), now, 180, 600) == "desk_idle"
    assert desk_block_reason(_desk_doc(now, idle_seconds=600), now, 180, 600) is None
    old = now - timedelta(seconds=181)
    assert desk_block_reason(_desk_doc(now, last_seen_at=old), now, 180, 600) == "desk_offline"
    assert desk_block_reason({**_desk_doc(now), "device": None}, now, 180, 600) == "desk_offline"
    assert desk_block_reason({"kind": "tag", "uuid": "x"}, now, 180, 600) is None
    assert desk_block_reason({"uuid": "x"}, now, 180, 600) is None  # legacy docs are tags


def test_evaluate_signal_reasons_and_presence_reason():
    now = utcnow()
    desk = _desk_doc(now)
    r = {"uuid": DEFAULT_DESK_UUID, "major": 1, "minor": 1, "rssi": -60}
    sig = evaluate_signal([r], [desk], -80, now)
    assert (sig.near, sig.reason, sig.best_rssi) == (True, "near", -60)
    assert evaluate_signal([], [desk], -80, now).reason == "no_signal"
    assert evaluate_signal([{**r, "rssi": -95}], [desk], -80, now).reason == "weak_signal"
    locked = _desk_doc(now, locked=True)
    sig = evaluate_signal([r], [locked], -80, now)
    assert (sig.near, sig.reason, sig.best_rssi) == (False, "desk_locked", -60)
    # not required -> the desk state is ignored
    assert evaluate_signal([r], [locked], -80, now, require_desk_activity=False).near
    # a weak reading of a locked desk is just weak
    assert evaluate_signal([{**r, "rssi": -95}], [locked], -80, now).reason == "weak_signal"
    # a tag alongside a locked desk still counts
    tag = {"kind": "tag", "uuid": DEFAULT_DESK_UUID, "major": 2, "minor": 2}
    both = evaluate_signal([r, {**r, "major": 2, "minor": 2}], [locked, tag], -80, now)
    assert both.near

    assert presence_reason(False, sig, True) == "outside"
    assert presence_reason(True, sig, True) == "desk_locked"
    assert presence_reason(True, sig, False) is None
    assert presence_reason(True, both, False) == "near"


# ---------- desk-aware presence through the API ----------


@pytest.fixture
def desk_employee(client, admin_token, office_id, strict):
    """Alice, face-enrolled, whose beacon is her (paired) desk computer."""
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    desk = make_desk(client, admin_token, office_id, assigned_employee_id="EMP0001")
    device_token = pair_desk(client, admin_token, desk["id"])
    beat(client, device_token)
    return {
        "token": token,
        "desk": desk,
        "device": device_token,
        "reading": reading(-60, uuid=DEFAULT_DESK_UUID, major=desk["major"], minor=desk["minor"]),
    }


def _check_in(client, e, where=INSIDE):
    verify(client, e["token"])
    return client.post(
        "/api/attendance/check-in",
        headers=auth(e["token"]),
        json={**where, "beacons": [e["reading"]]},
    )


def _hb(client, e, where=INSIDE):
    return client.post(
        "/api/attendance/heartbeat",
        headers=auth(e["token"]),
        json={**where, "beacons": [e["reading"]]},
    )


def test_unlocked_active_desk_is_present(client, desk_employee):
    assert _check_in(client, desk_employee).status_code == 201
    r = _hb(client, desk_employee)
    assert r.json()["state"] == "present" and r.json()["reason"] == "near"


def test_locked_desk_blocks_check_in(client, desk_employee):
    beat(client, desk_employee["device"], locked=True)
    r = _check_in(client, desk_employee)
    assert r.status_code == 403 and "locked" in r.json()["detail"]
    beat(client, desk_employee["device"], locked=False)
    assert _check_in(client, desk_employee).status_code == 201


def test_idle_and_offline_desk_block_check_in(client, desk_employee, db):
    beat(client, desk_employee["device"], idle=601)
    r = _check_in(client, desk_employee)
    assert r.status_code == 403 and "idle" in r.json()["detail"]
    asyncio.run(
        db.beacons.update_many(
            {"kind": "desk_app"}, {"$set": {"device.last_seen_at": utcnow() - timedelta(hours=1)}}
        )
    )
    r = _check_in(client, desk_employee)
    assert r.status_code == 403 and "not reporting" in r.json()["detail"]


def test_desk_state_drives_presence_and_log_reasons(client, desk_employee, db, admin_token):
    assert _check_in(client, desk_employee).status_code == 201
    token = desk_employee["device"]

    beat(client, token, locked=True)
    r = _hb(client, desk_employee).json()
    assert (r["state"], r["reason"]) == ("away", "desk_locked")

    beat(client, token, locked=False, idle=900)
    r = _hb(client, desk_employee).json()
    assert (r["state"], r["reason"]) == ("away", "desk_idle")

    asyncio.run(
        db.beacons.update_many(
            {"kind": "desk_app"}, {"$set": {"device.last_seen_at": utcnow() - timedelta(hours=1)}}
        )
    )
    r = _hb(client, desk_employee).json()
    assert (r["state"], r["reason"]) == ("away", "desk_offline")

    beat(client, token, locked=False, idle=5)
    r = _hb(client, desk_employee).json()
    assert (r["state"], r["reason"]) == ("present", "near")

    r = client.post(
        "/api/attendance/heartbeat",
        headers=auth(desk_employee["token"]),
        json={**INSIDE, "beacons": []},
    ).json()
    assert (r["state"], r["reason"]) == ("away", "no_signal")

    # the board and the session detail explain the away time
    row = next(
        x
        for x in client.get("/api/manager/presence", headers=auth(admin_token)).json()
        if x["employee_id"] == "EMP0001"
    )
    assert row["reason"] == "no_signal"
    session_id = row["session_id"]
    detail = client.get(f"/api/manager/sessions/{session_id}", headers=auth(admin_token)).json()
    assert [e["reason"] for e in detail["presence_log"]] == [
        "desk_locked",
        "desk_idle",
        "desk_offline",
        "near",
        "no_signal",
    ]

    r = _hb(client, desk_employee, where=OUTSIDE).json()
    assert (r["state"], r["reason"]) == ("outside", "outside")


def test_require_desk_activity_off_ignores_desk_state(client, desk_employee, settings):
    settings.require_desk_activity = False
    beat(client, desk_employee["device"], locked=True)
    assert _check_in(client, desk_employee).status_code == 201
    assert _hb(client, desk_employee).json()["state"] == "present"


def test_tags_are_unaffected_by_desk_rules(client, admin_token, office_id, strict):
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    make_beacon(client, admin_token, office_id, assigned_employee_id="EMP0001")
    verify(client, token)
    r = client.post(
        "/api/attendance/check-in",
        headers=auth(token),
        json={**INSIDE, "beacons": [reading()]},
    )
    assert r.status_code == 201
    r = client.post(
        "/api/attendance/heartbeat",
        headers=auth(token),
        json={**INSIDE, "beacons": [reading()]},
    ).json()
    assert (r["state"], r["reason"]) == ("present", "near")


def test_reason_is_null_without_bluetooth_requirement(client, office_id, settings):
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    verify(client, token)
    client.post("/api/attendance/check-in", headers=auth(token), json={**INSIDE, "beacons": []})
    r = client.post(
        "/api/attendance/heartbeat", headers=auth(token), json={**INSIDE, "beacons": []}
    ).json()
    assert (r["state"], r["reason"]) == ("present", None)


# ---------- dashboard and consent ----------


def test_dashboard_counts_unpaired_desk_apps_for_admins(client, admin_token, office_id):
    make_desk(client, admin_token, office_id, name="A")
    paired = make_desk(client, admin_token, office_id, name="B")
    make_desk(client, admin_token, office_id, name="C", active=False)
    make_beacon(client, admin_token, office_id, name="tag")
    pair_desk(client, admin_token, paired["id"])
    body = client.get("/api/manager/dashboard", headers=auth(admin_token)).json()
    assert body["unpaired_desk_apps"] == 1

    manager = register(client, office_id, "Manny", "manny@example.com")["access_token"]
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"})
    body = client.get("/api/manager/dashboard", headers=auth(manager)).json()
    assert body["unpaired_desk_apps"] is None


def test_consent_text_mentions_desk_app_only_when_used(client, admin_token, office_id):
    before = client.get("/api/consent/text").json()["body_markdown"]
    assert "Attendify Desk" not in before and "{" not in before
    make_desk(client, admin_token, office_id)
    after = client.get("/api/consent/text").json()["body_markdown"]
    assert "Attendify Desk" in after
    for promise in ("locked", "keyboard or mouse", "keystrokes", "screenshots", "window titles"):
        assert promise in after
