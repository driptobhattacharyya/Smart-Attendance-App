"""Check-in / heartbeat / check-out through the HTTP API: happy paths and every rejection code."""

import asyncio
import uuid

import pytest

from app.attendance.trust import FakeIntegrityVerifier, IntegrityVerdict

from .helpers import (
    API,
    HQ,
    TAG_UUID,
    check_in,
    check_out,
    event_types,
    heartbeat,
    loc,
    verify_face,
)

pytestmark = pytest.mark.asyncio


def err(r):
    return r.json()["error"]["code"]


# ---------------------------------------------------------------------- happy path


async def test_full_day_flow(world, client, owner_conn):
    h = world.headers["emp1"]
    st = (await client.get(f"{API}/attendance/status", headers=h)).json()
    assert st["checked_in"] is False and st["face_enrolled"] is True
    assert st["requirements"] == {"face": True, "geofence": True, "proximity": False}
    assert st["heartbeat_interval_seconds"] == 60 and st["face_verified_until"] is None

    r = await check_in(client, world, "emp1")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session"]["mode"] == "office" and body["session"]["open"] is True
    assert body["today"]["status"] == "present" and "open" in body["today"]["flags"]
    assert body["integrity"] == "unverified"

    st = (await client.get(f"{API}/attendance/status", headers=h)).json()
    assert st["checked_in"] and st["presence"] == "present" and st["session"]["state"] == "present"
    assert st["face_verified_until"] is None  # consumed by the check-in

    hb = await heartbeat(client, world, "emp1")
    assert hb.status_code == 200 and hb.json()["state"] == "present"
    assert hb.json()["checked_in"] is True and hb.json()["next_heartbeat_seconds"] == 60

    out = await check_out(client, world, "emp1")
    assert out.status_code == 200, out.text
    assert out.json()["session"]["open"] is False
    assert out.json()["today"]["last_out"] is not None

    me = (await client.get(f"{API}/attendance/me", headers=h)).json()
    assert len(me["days"]) == 1 and me["days"][0]["sessions"][0]["open"] is False
    assert await event_types(owner_conn, world.users["emp1"]["id"]) == [
        "face_verified",
        "check_in",
        "check_out",
    ]
    assert (await check_out(client, world, "emp1")).status_code == 409


async def test_office_alias_and_default_mode(world, client):
    r = await check_in(client, world, "emp1", json={"mode": "office"})
    assert r.status_code == 201 and r.json()["session"]["mode"] == "office"


# ------------------------------------------------------------------- consent / auth


async def test_consent_required_everywhere(team_org, client):
    h = team_org.headers["emp1"]
    for method, path in [
        ("GET", "/attendance/status"),
        ("POST", "/attendance/check-in"),
        ("POST", "/attendance/heartbeat"),
        ("POST", "/attendance/check-out"),
        ("GET", "/attendance/me"),
        ("GET", "/face/challenge"),
        ("GET", "/attendance/integrity-nonce"),
    ]:
        r = await client.request(
            method, f"{API}{path}", json={} if method == "POST" else None, headers=h
        )
        assert r.status_code == 403 and err(r) == "consent_required", path
    assert (await client.get(f"{API}/attendance/status")).status_code == 401


# --------------------------------------------------------------------- rejections


async def test_face_required(world, client):
    r = await check_in(client, world, "emp1", verify=False)
    assert r.status_code == 403 and err(r) == "face_required"


async def test_face_scan_is_good_for_one_check_in(world, client):
    assert (await check_in(client, world, "emp1")).status_code == 201
    assert (await check_out(client, world, "emp1")).status_code == 200
    r = await check_in(client, world, "emp1", verify=False)
    assert err(r) == "face_required"


async def test_face_scan_expires(world, client, clock):
    assert (await verify_face(client, world, "emp1")).status_code == 200
    clock.advance(minutes=11)
    r = await check_in(client, world, "emp1", verify=False)
    assert err(r) == "face_required"


async def test_outside_geofence(world, client):
    r = await check_in(client, world, "emp1", json={"location": loc(HQ["lat"] + 0.01, HQ["lon"])})
    assert r.status_code == 403 and err(r) == "outside_geofence"
    assert r.json()["error"]["details"]["radius_m"] == 150
    # the face scan survives a rejected attempt
    assert (await check_in(client, world, "emp1", verify=False)).status_code == 201


async def test_location_rules(world, client, clock):
    h = world.headers["emp1"]
    cases = [
        ({"location": None}, 400, "location_required"),
        ({"location": loc(mock=True)}, 403, "mock_location"),
        ({"location": loc(accuracy_m=250)}, 403, "location_inaccurate"),
        (
            {"location": loc(captured_at="2000-01-01T00:00:00Z")},
            403,
            "location_stale",
        ),
    ]
    for body, status, code in cases:
        r = await client.post(
            f"{API}/attendance/check-in", json={"mode": "onsite", **body}, headers=h
        )
        assert (r.status_code, err(r)) == (status, code), body


async def test_mock_location_is_audited_even_though_rejected(world, client):
    r = await check_in(client, world, "emp1", json={"location": loc(mock=True)})
    assert err(r) == "mock_location"
    audit = (
        await client.get(f"{API}/audit?action=attendance.rejected", headers=world.owner_headers)
    ).json()["items"]
    assert audit and audit[0]["after"]["code"] == "mock_location"
    assert audit[0]["actor_user_id"] == world.users["emp1"]["id"]


async def test_rooted_device_blocked_by_default_and_configurable(world, client):
    dev = {"device": {"platform": "android", "app_version": "1.0", "rooted": True}}
    r = await check_in(client, world, "emp1", json=dev)
    assert r.status_code == 403 and err(r) == "rooted_device"
    r = await client.patch(
        f"{API}/org",
        json={"settings": {"block_rooted_devices": False}},
        headers=world.owner_headers,
    )
    assert r.status_code == 200
    assert (await check_in(client, world, "emp1", verify=False, json=dev)).status_code == 201


async def test_already_checked_in_and_not_checked_in(world, client):
    assert (await heartbeat(client, world, "emp1")).status_code == 409
    assert (await check_in(client, world, "emp1")).status_code == 201
    r = await check_in(client, world, "emp1")
    assert r.status_code == 409 and err(r) == "already_checked_in"


async def test_no_office(world, client):
    await client.patch(
        f"{API}/people/{world.users['emp1']['id']}",
        json={"office_id": None},
        headers=world.owner_headers,
    )
    r = await check_in(client, world, "emp1")
    assert r.status_code == 409 and err(r) == "no_office"


async def test_geofence_module_off_skips_distance(world, client):
    await client.patch(
        f"{API}/org", json={"modules": {"geofence": False}}, headers=world.owner_headers
    )
    r = await check_in(client, world, "emp1", json={"location": loc(HQ["lat"] + 1, HQ["lon"])})
    assert r.status_code == 201
    # ...but a fake location is still refused
    await check_out(client, world, "emp1")
    r = await check_in(client, world, "emp1", json={"location": loc(mock=True)})
    assert err(r) == "mock_location"


async def test_face_module_off_needs_no_scan(world, client):
    await client.patch(f"{API}/org", json={"modules": {"face": False}}, headers=world.owner_headers)
    assert (await check_in(client, world, "emp1", verify=False)).status_code == 201


# ------------------------------------------------------------------------- remote / WFH


async def test_remote_needs_wfh_module_and_approval(world, client, clock):
    body = {"mode": "remote", "location": None}
    r = await check_in(client, world, "emp1", json=body)
    assert r.status_code == 403 and err(r) == "module_disabled"
    await client.patch(f"{API}/org", json={"modules": {"wfh": True}}, headers=world.owner_headers)
    r = await check_in(client, world, "emp1", verify=False, json=body)
    assert err(r) == "wfh_not_allowed"

    today = (await client.get(f"{API}/attendance/status", headers=world.headers["emp1"])).json()[
        "today"
    ]["day"]
    req = await client.post(
        f"{API}/requests",
        json={"kind": "wfh", "start_date": today, "reason": "plumber visit"},
        headers=world.headers["emp1"],
    )
    assert req.status_code == 201, req.text
    # still pending: not allowed
    assert err(await check_in(client, world, "emp1", verify=False, json=body)) == "wfh_not_allowed"
    d = await client.post(
        f"{API}/requests/{req.json()['id']}/decision",
        json={"action": "approve"},
        headers=world.headers["lead"],
    )
    assert d.status_code == 200, d.text
    r = await check_in(client, world, "emp1", verify=False, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["session"]["mode"] == "remote" and "remote" in r.json()["today"]["flags"]
    # remote heartbeats never need a location
    hb = await client.post(f"{API}/attendance/heartbeat", json={}, headers=world.headers["emp1"])
    assert hb.json()["state"] == "present" and hb.json()["reason"] == "remote"


async def test_remote_still_requires_face(world, client):
    await client.patch(f"{API}/org", json={"modules": {"wfh": True}}, headers=world.owner_headers)
    today = (await client.get(f"{API}/attendance/status", headers=world.headers["emp1"])).json()[
        "today"
    ]["day"]
    import datetime

    wd = datetime.date.fromisoformat(today).weekday()
    r = await client.post(
        f"{API}/attendance/policies",
        json={
            "effective_from": today,
            "rules": {"wfh_weekdays": [wd], "work_start": "00:00", "work_end": "23:59"},
        },
        headers=world.owner_headers,
    )
    assert r.status_code == 201, r.text
    r = await check_in(client, world, "emp1", verify=False, json={"mode": "remote"})
    assert err(r) == "face_required"
    r = await check_in(client, world, "emp1", json={"mode": "remote"})
    assert r.status_code == 201  # wfh_weekdays needs no request


# ---------------------------------------------------------------------------- heartbeat


async def test_heartbeat_outside_auto_checks_out(world, client, owner_conn):
    await check_in(client, world, "emp1")
    far = loc(HQ["lat"] + 0.02, HQ["lon"])
    r = await heartbeat(client, world, "emp1", json={"location": far})
    assert r.status_code == 200
    assert r.json()["state"] == "outside" and r.json()["checked_in"] is False
    types = await event_types(owner_conn, world.users["emp1"]["id"])
    assert types[-3:] == ["check_in", "presence_changed", "auto_check_out"]
    st = (await client.get(f"{API}/attendance/status", headers=world.headers["emp1"])).json()
    assert st["checked_in"] is False
    assert any("auto_checked_out" in d["flags"] for d in [st["today"]])


async def test_heartbeat_mock_location_closes_and_is_audited(world, client):
    await check_in(client, world, "emp1")
    r = await heartbeat(client, world, "emp1", json={"location": loc(mock=True)})
    assert r.json()["state"] == "outside" and r.json()["reason"] == "mock_location"
    audit = (
        await client.get(f"{API}/audit?action=attendance.rejected", headers=world.owner_headers)
    ).json()["items"]
    assert audit and audit[0]["after"]["code"] == "mock_location"


async def test_unreliable_fix_changes_nothing(world, client, owner_conn):
    await check_in(client, world, "emp1")
    for bad in (loc(accuracy_m=500), loc(captured_at="2000-01-01T00:00:00Z")):
        r = await heartbeat(client, world, "emp1", json={"location": bad})
        assert r.status_code == 200 and r.json()["reason"] == "location_unreliable"
        assert r.json()["state"] == "present" and r.json()["checked_in"] is True
    r = await client.post(f"{API}/attendance/heartbeat", json={}, headers=world.headers["emp1"])
    assert r.json()["checked_in"] is True  # no location at all: also keeps the session
    assert "presence_changed" not in await event_types(owner_conn, world.users["emp1"]["id"])
    n = await owner_conn.fetchval("SELECT count(*) FROM presence_samples")
    assert n == 3


async def test_stale_session_is_closed_at_last_evidence(world, client, clock, owner_conn):
    await check_in(client, world, "emp1")
    clock.advance(minutes=40)
    r = await heartbeat(client, world, "emp1")
    assert r.status_code == 409 and err(r) == "session_expired"
    assert r.json()["error"]["details"]["reason"] == "stale"
    # the auto check-out committed (the 409 is a returned response, not a rollback)
    assert (await event_types(owner_conn, world.users["emp1"]["id"]))[-1] == "auto_check_out"
    assert (await heartbeat(client, world, "emp1")).status_code == 409
    again = await check_in(client, world, "emp1")  # can start fresh
    assert again.status_code == 201


async def test_check_in_replaces_a_lapsed_session(world, client, clock, owner_conn):
    await check_in(client, world, "emp1")
    clock.advance(hours=2)
    assert (await check_in(client, world, "emp1")).status_code == 201
    types = await event_types(owner_conn, world.users["emp1"]["id"])
    assert types.count("auto_check_out") == 1 and types.count("check_in") == 2


async def test_checkout_of_lapsed_session_closes_it_quietly(world, client, clock):
    await check_in(client, world, "emp1")
    clock.advance(hours=3)
    r = await check_out(client, world, "emp1")
    assert r.status_code == 200 and r.json()["session"]["open"] is False


async def test_max_session_length(world, client, clock, owner_conn):
    await check_in(client, world, "emp1")
    for _ in range(18):  # keep it alive with heartbeats for 18 hours
        clock.advance(minutes=60)
        r = await heartbeat(client, world, "emp1")
        if r.status_code == 409:
            break
    assert r.status_code == 409
    row = await owner_conn.fetchrow(
        "SELECT payload FROM attendance_events WHERE type = 'auto_check_out'"
    )
    assert row is not None


# --------------------------------------------------------------------------- idempotency


async def test_check_in_retry_with_same_key_does_not_double_record(world, client, owner_conn):
    await verify_face(client, world, "emp1")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    a = await check_in(client, world, "emp1", verify=False, headers=key)
    b = await check_in(client, world, "emp1", verify=False, headers=key)
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    types = await event_types(owner_conn, world.users["emp1"]["id"])
    assert types.count("check_in") == 1


async def test_idempotency_key_reuse_with_other_body_is_422(world, client):
    key = {"Idempotency-Key": str(uuid.uuid4())}
    await check_in(client, world, "emp1", headers=key)
    r = await heartbeat(client, world, "emp1", headers=key)
    assert r.status_code == 422 and err(r) == "idempotency_key_reuse"


async def test_idempotency_keys_are_per_user(world, client, owner_conn):
    key = {"Idempotency-Key": str(uuid.uuid4())}
    await check_in(client, world, "emp1", headers=key)
    r = await check_in(client, world, "emp2", headers=key)
    # emp2 must never be handed emp1's stored response
    assert r.status_code == 422 and err(r) == "idempotency_key_reuse"


async def test_heartbeat_retry_with_key_records_one_sample(world, client, owner_conn):
    await check_in(client, world, "emp1")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    a = await heartbeat(client, world, "emp1", headers=key)
    b = await heartbeat(client, world, "emp1", headers=key)
    assert a.json() == b.json()
    assert await owner_conn.fetchval("SELECT count(*) FROM presence_samples") == 1


async def test_rejections_are_not_remembered_under_a_key(world, client):
    key = {"Idempotency-Key": str(uuid.uuid4())}
    r = await check_in(client, world, "emp1", verify=False, headers=key)
    assert err(r) == "face_required"
    assert (await verify_face(client, world, "emp1")).status_code == 200
    r = await check_in(client, world, "emp1", verify=False, headers=key)
    assert r.status_code == 201


# ---------------------------------------------------------------------------- concurrency


async def test_simultaneous_check_ins_open_exactly_one_session(world, client, owner_conn):
    await verify_face(client, world, "emp1")
    results = await asyncio.gather(
        *[check_in(client, world, "emp1", verify=False) for _ in range(6)]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [409] * 5, codes
    types = await event_types(owner_conn, world.users["emp1"]["id"])
    assert types.count("check_in") == 1


async def test_simultaneous_heartbeats_do_not_corrupt_state(world, client, owner_conn):
    await check_in(client, world, "emp1")
    away = {"beacons": []}
    results = await asyncio.gather(*[heartbeat(client, world, "emp1", json=away) for _ in range(8)])
    assert all(r.status_code == 200 for r in results)
    assert await owner_conn.fetchval("SELECT count(*) FROM presence_samples") == 8
    # an outside heartbeat racing with present ones ends with exactly one close
    far = loc(HQ["lat"] + 0.05, HQ["lon"])
    results = await asyncio.gather(
        heartbeat(client, world, "emp1", json={"location": far}),
        heartbeat(client, world, "emp1"),
        heartbeat(client, world, "emp1", json={"location": far}),
    )
    assert {r.status_code for r in results} <= {200, 409}
    types = await event_types(owner_conn, world.users["emp1"]["id"])
    assert types.count("auto_check_out") == 1 and types.count("check_in") == 1


async def test_check_in_racing_with_check_out(world, client, owner_conn):
    await check_in(client, world, "emp1")
    await verify_face(client, world, "emp1")
    results = await asyncio.gather(
        check_out(client, world, "emp1"), check_in(client, world, "emp1", verify=False)
    )
    types = await event_types(owner_conn, world.users["emp1"]["id"])
    assert types.count("check_out") == 1
    assert sorted(types.count(t) for t in ("check_in",)) in ([1], [2])
    assert all(r.status_code in (200, 201, 409) for r in results)


# --------------------------------------------------------------------- consent withdraw


async def test_withdrawing_consent_checks_the_user_out(world, client, owner_conn):
    await check_in(client, world, "emp1")
    r = await client.post(f"{API}/auth/consent/withdraw", headers=world.headers["emp1"])
    assert r.status_code == 200
    row = await owner_conn.fetchrow(
        "SELECT source, payload FROM attendance_events WHERE type = 'check_out'"
    )
    assert row["source"] == "system" and "consent_withdrawn" in row["payload"]
    assert (await heartbeat(client, world, "emp1")).status_code == 403  # consent_required now


# ---------------------------------------------------------------------- Play Integrity


async def test_play_integrity_flow(world, client, app):
    verifier = FakeIntegrityVerifier(
        {
            "good": IntegrityVerdict(True),
            "bad": IntegrityVerdict(False, "device_integrity_failed"),
            "down": IntegrityVerdict(False, "verification_unavailable", unavailable=True),
        }
    )
    app.state.integrity_verifier = verifier
    h = world.headers["emp1"]

    async def nonce():
        return (await client.get(f"{API}/attendance/integrity-nonce", headers=h)).json()["nonce"]

    def dev(token, n, platform="android"):
        return {"device": {"platform": platform, "integrity_token": token, "integrity_nonce": n}}

    r = await check_in(client, world, "emp1", json={"device": {"platform": "android"}})
    assert err(r) == "integrity_required"
    r = await check_in(client, world, "emp1", verify=False, json=dev("good", None))
    assert err(r) == "integrity_failed"  # no nonce
    r = await check_in(client, world, "emp1", verify=False, json=dev("bad", await nonce()))
    assert err(r) == "integrity_failed" and r.json()["error"]["details"]["reason"] == (
        "device_integrity_failed"
    )
    r = await check_in(client, world, "emp1", verify=False, json=dev("down", await nonce()))
    assert r.status_code == 503 and err(r) == "integrity_unavailable"
    used = await nonce()
    r = await check_in(client, world, "emp1", verify=False, json=dev("good", used))
    assert r.status_code == 201 and r.json()["integrity"] == "verified"
    assert verifier.calls[-1] == "good"
    await check_out(client, world, "emp1")
    # a nonce is single use
    await verify_face(client, world, "emp1")
    r = await check_in(client, world, "emp1", verify=False, json=dev("good", used))
    assert err(r) == "integrity_failed"
    # a nonce of another user is refused
    other = (
        await client.get(f"{API}/attendance/integrity-nonce", headers=world.headers["emp2"])
    ).json()["nonce"]
    r = await check_in(client, world, "emp1", verify=False, json=dev("good", other))
    assert err(r) == "integrity_failed"
    # iOS devices are not subject to Play Integrity
    r = await check_in(client, world, "emp1", verify=False, json={"device": {"platform": "ios"}})
    assert r.status_code == 201 and r.json()["integrity"] == "not_applicable"


# ------------------------------------------------------------------- desk proximity


async def _proximity_world(world, client, **policy):
    await client.patch(
        f"{API}/org",
        json={"modules": {"proximity": True, "desk_app": True}},
        headers=world.owner_headers,
    )
    from datetime import date

    rules = {"require_proximity": True, "work_start": "00:00", "work_end": "23:59", **policy}
    r = await client.post(
        f"{API}/attendance/policies",
        json={"effective_from": date.today().isoformat(), "rules": rules},
        headers=world.owner_headers,
    )
    assert r.status_code == 201, r.text


async def _tag(client, world, major=7, minor=9, **extra):
    r = await client.post(
        f"{API}/places/desk-devices",
        json={
            "kind": "tag",
            "name": "Desk tag",
            "ibeacon_uuid": TAG_UUID,
            "major": major,
            "minor": minor,
            "office_id": world.office["id"],
            **extra,
        },
        headers=world.owner_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def beacon(rssi=-60, major=7, minor=9, uuid_=TAG_UUID):
    return {"uuid": uuid_, "major": major, "minor": minor, "rssi": rssi}


async def test_proximity_requires_a_device_and_a_signal(world, client):
    await _proximity_world(world, client)
    r = await check_in(client, world, "emp1")
    assert r.status_code == 409 and err(r) == "no_desk_device"
    await _tag(client, world)
    r = await check_in(client, world, "emp1", verify=False, json={"beacons": []})
    assert r.status_code == 403 and err(r) == "desk_not_near"
    r = await check_in(client, world, "emp1", verify=False, json={"beacons": [beacon(-95)]})
    assert err(r) == "desk_not_near"  # too weak
    r = await check_in(
        client, world, "emp1", verify=False, json={"beacons": [beacon(major=1, minor=1)]}
    )
    assert err(r) == "desk_not_near"  # someone else's tag
    r = await check_in(client, world, "emp1", verify=False, json={"beacons": [beacon(-60)]})
    assert r.status_code == 201 and r.json()["best_rssi"] == -60


async def test_proximity_heartbeat_goes_away_and_back(world, client, owner_conn):
    await _proximity_world(world, client)
    await _tag(client, world)
    await check_in(client, world, "emp1", json={"beacons": [beacon()]})
    r = await heartbeat(client, world, "emp1", json={"beacons": []})
    assert r.json()["state"] == "away" and r.json()["reason"] == "no_signal"
    r = await heartbeat(client, world, "emp1", json={"beacons": []})
    assert r.json()["state"] == "away"
    r = await heartbeat(client, world, "emp1", json={"beacons": [beacon()]})
    assert r.json()["state"] == "present" and r.json()["reason"] == "near"
    types = await event_types(owner_conn, world.users["emp1"]["id"])
    assert types.count("presence_changed") == 2  # only transitions become events
    assert await owner_conn.fetchval("SELECT count(*) FROM presence_samples") == 3


async def test_desk_app_rotating_id_activity_rules(world, client, owner_conn):
    import base64
    import time

    from app.places.identity import rotating_ids, window_for

    await _proximity_world(world, client, desk_activity="locked_or_idle", desk_idle_limit_minutes=5)
    await client.patch(
        f"{API}/org",
        json={"settings": {"desk_activity_monitoring": True}},
        headers=world.owner_headers,
    )
    dev = (
        await client.post(
            f"{API}/places/desk-devices",
            json={
                "kind": "desk_app",
                "name": "PC",
                "office_id": world.office["id"],
                "assigned_user_id": world.users["emp1"]["id"],
            },
            headers=world.owner_headers,
        )
    ).json()
    code = (
        await client.post(
            f"{API}/places/desk-devices/{dev['id']}/pairing-code", headers=world.owner_headers
        )
    ).json()["code"]
    paired = (await client.post(f"{API}/desk/pair", json={"code": code})).json()
    secret = base64.b64decode(paired["secret"])
    desk_uuid = paired["beacon"]["uuid"]
    token = {"Authorization": f"Device {paired['device_token']}"}

    def reading():
        major, minor = rotating_ids(secret, window_for(time.time()))
        return {"uuid": desk_uuid, "major": major, "minor": minor, "rssi": -55}

    async def desk(**state):
        r = await client.post(f"{API}/desk/heartbeat", json=state, headers=token)
        assert r.status_code == 200

    async def attempt():
        return await check_in(client, world, "emp1", verify=False, json={"beacons": [reading()]})

    await verify_face(client, world, "emp1")
    await desk(locked=True, idle_seconds=0)
    assert err(await attempt()) == "desk_locked"
    await desk(locked=False, idle_seconds=3600)
    assert err(await attempt()) == "desk_idle"
    await owner_conn.execute("UPDATE desk_devices SET last_seen_at = now() - interval '1 hour'")
    assert err(await attempt()) == "desk_offline"
    await desk(locked=False, idle_seconds=10)
    r = await attempt()
    assert r.status_code == 201, r.text
    # a replayed old window does not match (cloned broadcaster)
    await check_out(client, world, "emp1")
    await verify_face(client, world, "emp1")
    old = reading()
    old["major"], old["minor"] = rotating_ids(secret, window_for(time.time()) - 5)
    r = await check_in(client, world, "emp1", verify=False, json={"beacons": [old]})
    assert err(r) == "desk_not_near"


async def test_desk_activity_ignored_unless_org_opted_in(world, client):
    import base64
    import time

    from app.places.identity import rotating_ids, window_for

    await _proximity_world(world, client, desk_activity="locked_only")  # policy yes, org opt-in no
    await client.patch(
        f"{API}/org", json={"modules": {"desk_app": True}}, headers=world.owner_headers
    )
    dev = (
        await client.post(
            f"{API}/places/desk-devices",
            json={"kind": "desk_app", "name": "PC", "assigned_user_id": world.users["emp1"]["id"]},
            headers=world.owner_headers,
        )
    ).json()
    code = (
        await client.post(
            f"{API}/places/desk-devices/{dev['id']}/pairing-code", headers=world.owner_headers
        )
    ).json()["code"]
    paired = (await client.post(f"{API}/desk/pair", json={"code": code})).json()
    await client.post(
        f"{API}/desk/heartbeat",
        json={"locked": True},
        headers={"Authorization": f"Device {paired['device_token']}"},
    )
    major, minor = rotating_ids(base64.b64decode(paired["secret"]), window_for(time.time()))
    r = await check_in(
        client,
        world,
        "emp1",
        json={
            "beacons": [
                {"uuid": paired["beacon"]["uuid"], "major": major, "minor": minor, "rssi": -50}
            ]
        },
    )
    assert r.status_code == 201
