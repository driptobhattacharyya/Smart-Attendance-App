import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.config import Settings
from app.presence import (
    SessionClosedError,
    apply_presence,
    best_rssi,
    credit_interval,
    decide_state,
    is_near,
    reading_matches,
    worked_seconds,
)

from .conftest import (
    DESK_UUID,
    INSIDE,
    OUTSIDE,
    ZONE_UUID,
    auth,
    enroll,
    make_beacon,
    reading,
    register,
    verify,
)

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


# ---------- pure functions ----------


def test_matching_rules():
    beacon = {"uuid": DESK_UUID.lower(), "major": 7, "minor": 9}
    assert reading_matches({"uuid": DESK_UUID, "major": 7, "minor": 9, "rssi": -50}, beacon)
    # case-insensitive, with or without dashes
    assert reading_matches({"uuid": DESK_UUID.lower(), "major": 7, "minor": 9}, beacon)
    assert reading_matches({"uuid": DESK_UUID.replace("-", ""), "major": 7, "minor": 9}, beacon)
    assert not reading_matches({"uuid": DESK_UUID, "major": 7, "minor": 10}, beacon)
    assert not reading_matches({"uuid": DESK_UUID, "major": 8, "minor": 9}, beacon)
    assert not reading_matches({"uuid": DESK_UUID, "major": None, "minor": 9}, beacon)
    assert not reading_matches({"uuid": ZONE_UUID, "major": 7, "minor": 9}, beacon)
    assert not reading_matches({"uuid": "garbage", "major": 7, "minor": 9}, beacon)
    assert not reading_matches({"major": 7, "minor": 9}, beacon)


def test_null_major_minor_are_wildcards():
    any_minor = {"uuid": DESK_UUID, "major": 7, "minor": None}
    assert reading_matches({"uuid": DESK_UUID, "major": 7, "minor": 123}, any_minor)
    assert not reading_matches({"uuid": DESK_UUID, "major": 6, "minor": 123}, any_minor)
    anything = {"uuid": DESK_UUID, "major": None, "minor": None}
    assert reading_matches({"uuid": DESK_UUID, "major": 1, "minor": 2}, anything)
    assert reading_matches({"uuid": DESK_UUID, "major": None, "minor": None}, anything)


def test_best_rssi_and_threshold():
    beacons = [{"uuid": DESK_UUID, "major": 1, "minor": 1}]
    readings = [
        {"uuid": DESK_UUID, "major": 1, "minor": 1, "rssi": -90},
        {"uuid": DESK_UUID, "major": 1, "minor": 1, "rssi": -70},
        {"uuid": ZONE_UUID, "major": 1, "minor": 1, "rssi": -30},  # someone else's beacon
    ]
    assert best_rssi(readings, beacons) == -70
    assert best_rssi([], beacons) is None
    assert best_rssi(readings, []) is None
    assert is_near(-80, -80) is True  # boundary is inclusive
    assert is_near(-81, -80) is False
    assert is_near(None, -80) is False


def test_decide_state():
    assert decide_state(False, True, True) == "outside"
    assert decide_state(False, False, False) == "outside"
    assert decide_state(True, True, True) == "present"
    assert decide_state(True, False, True) == "away"
    assert decide_state(True, False, False) == "present"  # bluetooth not required


def test_credit_interval():
    grace = 15 * 60
    assert credit_interval("present", T0, T0 + timedelta(minutes=5), grace) == (300, 0)
    assert credit_interval("away", T0, T0 + timedelta(minutes=5), grace) == (0, 300)
    # exactly at the grace limit still counts fully; beyond it is capped, the rest is dropped
    assert credit_interval("present", T0, T0 + timedelta(minutes=15), grace) == (900, 0)
    assert credit_interval("present", T0, T0 + timedelta(hours=3), grace) == (900, 0)
    assert credit_interval("away", T0, T0 + timedelta(hours=3), grace) == (0, 900)
    # clock going backwards never credits negative time
    assert credit_interval("present", T0, T0 - timedelta(minutes=1), grace) == (0, 0)


def test_worked_seconds():
    grace, cap = 900, 14 * 3600
    open_present = {"present_seconds": 600, "last_state": "present", "last_heartbeat_at": T0}
    assert worked_seconds(open_present, T0 + timedelta(minutes=5), grace, cap) == 900
    # the open tail is capped by the grace window
    assert worked_seconds(open_present, T0 + timedelta(hours=2), grace, cap) == 600 + 900
    # no tail while away or outside
    assert (
        worked_seconds(
            {**open_present, "last_state": "away"}, T0 + timedelta(minutes=5), grace, cap
        )
        == 600
    )
    # closed sessions only count what was credited
    closed = {**open_present, "ended_at": T0 + timedelta(hours=1)}
    assert worked_seconds(closed, T0 + timedelta(hours=1), grace, cap) == 600
    # open sessions are capped overall
    huge = {"present_seconds": 20 * 3600, "last_state": "away", "last_heartbeat_at": T0}
    assert worked_seconds(huge, T0, grace, cap) == cap


# ---------- atomic crediting ----------


class _RacingCollection:
    """Runs ``hook`` (a competing heartbeat) right before the first update_one."""

    def __init__(self, inner, hook):
        self._inner, self._hook = inner, hook

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def update_one(self, *args, **kwargs):
        hook, self._hook = self._hook, None
        if hook:
            await hook()
        return await self._inner.update_one(*args, **kwargs)


async def test_concurrent_heartbeats_do_not_double_credit(db):
    result = await db.attendance_sessions.insert_one(
        {
            "employee_id": "E1",
            "started_at": T0,
            "ended_at": None,
            "source": "app",
            "last_heartbeat_at": T0,
            "last_state": "present",
            "present_seconds": 0,
            "away_seconds": 0,
        }
    )
    sid = result.inserted_id
    now = T0 + timedelta(minutes=5)

    async def competing():
        await apply_presence(db, sid, now, 900, state="present")

    racing = SimpleNamespace(
        attendance_sessions=_RacingCollection(db.attendance_sessions, competing)
    )
    await apply_presence(racing, sid, now, 900, state="present")

    doc = await db.attendance_sessions.find_one({"_id": sid})
    assert doc["present_seconds"] == 300  # not 600


async def test_apply_presence_refuses_closed_session(db):
    result = await db.attendance_sessions.insert_one(
        {
            "started_at": T0,
            "ended_at": T0 + timedelta(minutes=1),
            "last_heartbeat_at": T0,
            "last_state": "present",
        }
    )
    with pytest.raises(SessionClosedError):
        await apply_presence(db, result.inserted_id, T0 + timedelta(minutes=5), 900, state="away")
    with pytest.raises(SessionClosedError):
        await apply_presence(db, ObjectId(), T0, 900, state="away")


async def test_presence_log_is_capped(db):
    result = await db.attendance_sessions.insert_one(
        {
            "started_at": T0,
            "ended_at": None,
            "last_heartbeat_at": T0,
            "last_state": "present",
            "present_seconds": 0,
            "away_seconds": 0,
        }
    )
    for i in range(1, 506):
        await apply_presence(
            db,
            result.inserted_id,
            T0 + timedelta(seconds=i),
            900,
            state="present",
            log_entry={"at": T0, "state": "present", "distance_m": 1.0, "best_rssi": i},
        )
    doc = await db.attendance_sessions.find_one({"_id": result.inserted_id})
    assert len(doc["presence_log"]) == 500
    assert doc["presence_log"][-1]["best_rssi"] == 505
    assert doc["present_seconds"] == 505


# ---------- API: check-in with beacons ----------


@pytest.fixture
def employee(client, admin_token, office_id, strict):
    """A registered, face-enrolled employee with a desk beacon (major 1, minor 1)."""
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    make_beacon(client, admin_token, office_id, assigned_employee_id="EMP0001")
    return token


def _check_in(client, token, beacons=None, where=INSIDE):
    verify(client, token)
    return client.post(
        "/api/attendance/check-in",
        headers=auth(token),
        json={**where, "beacons": [reading()] if beacons is None else beacons},
    )


def _beat(client, token, beacons=None, where=INSIDE):
    return client.post(
        "/api/attendance/heartbeat",
        headers=auth(token),
        json={**where, "beacons": [] if beacons is None else beacons},
    )


def test_check_in_without_beacon_or_with_weak_signal(client, employee):
    r = _check_in(client, employee, beacons=[])
    assert r.status_code == 403 and "beacon" in r.json()["detail"]
    r = _check_in(client, employee, beacons=[reading(rssi=-95)])
    assert r.status_code == 403 and "weak" in r.json()["detail"]
    # someone else's beacon does not count
    r = _check_in(client, employee, beacons=[reading(minor=2)])
    assert r.status_code == 403
    r = _check_in(client, employee, beacons=[reading(uuid=ZONE_UUID)])
    assert r.status_code == 403

    r = _check_in(client, employee, beacons=[reading(rssi=-80)])  # boundary
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "present" and body["minutes"] == 0
    assert body["present_minutes"] == 0 and body["away_minutes"] == 0


def test_check_in_matches_case_insensitively(client, employee):
    r = _check_in(client, employee, beacons=[reading(uuid=DESK_UUID.lower())])
    assert r.status_code == 201


def test_check_in_with_wildcard_beacon(client, admin_token, office_id, strict):
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    make_beacon(client, admin_token, office_id, major=None, minor=None)  # zone, any major/minor
    r = _check_in(client, token, beacons=[reading(major=42, minor=4242)])
    assert r.status_code == 201, r.text


def test_check_in_zone_beacon_fallback(client, admin_token, office_id, strict):
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    make_beacon(client, admin_token, office_id, name="Zone", uuid=ZONE_UUID, major=5, minor=None)
    r = _check_in(client, token, beacons=[reading(uuid=ZONE_UUID, major=5, minor=77)])
    assert r.status_code == 201, r.text


def test_check_in_without_any_beacon_configured_is_409(client, office_id, strict):
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    r = _check_in(client, token, beacons=[reading()])
    assert r.status_code == 409 and "No beacon configured" in r.json()["detail"]


def test_check_in_ignores_beacons_when_not_required(client, office_id, settings):
    settings.require_bluetooth = False
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    assert _check_in(client, token, beacons=[]).status_code == 201


def test_old_bluetooth_fields_are_gone(client, office_id, settings):
    settings.require_bluetooth = False
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    verify(client, token)
    r = client.post(
        "/api/attendance/check-in",
        headers=auth(token),
        json={**INSIDE, "bluetooth_device_id": "AA:BB", "bluetooth_rssi": -50},
    )
    assert r.status_code == 201  # unknown fields are ignored, not used


def test_check_in_validates_readings(client, employee):
    verify(client, employee)
    r = client.post(
        "/api/attendance/check-in",
        headers=auth(employee),
        json={**INSIDE, "beacons": [{"uuid": DESK_UUID, "major": 70000, "minor": 1, "rssi": -50}]},
    )
    assert r.status_code == 422


def test_session_starts_in_present_state(client, db, employee):
    _check_in(client, employee)
    doc = asyncio.run(db.attendance_sessions.find_one({"employee_id": "EMP0001"}))
    assert doc["last_state"] == "present"
    assert doc["present_seconds"] == 0 and doc["away_seconds"] == 0
    assert doc["last_heartbeat_at"] == doc["started_at"]
    status = client.get("/api/attendance/status", headers=auth(employee)).json()
    assert status["presence"] == "present"
    assert status["heartbeat_interval_seconds"] == 300


# ---------- API: heartbeats ----------


def test_heartbeat_requires_check_in(client, employee):
    assert _beat(client, employee).status_code == 409
    assert client.post("/api/attendance/heartbeat", json=INSIDE).status_code == 401


def test_present_away_present_crediting(client, clock, employee):
    _check_in(client, employee)

    clock.advance(minutes=5)  # time since check-in belongs to the previous state: present
    r = _beat(client, employee, beacons=[])  # no beacon heard now -> away
    body = r.json()
    assert body["state"] == "away" and body["checked_in"] is True
    assert body["next_heartbeat_seconds"] == 300
    assert body["session"]["present_minutes"] == 5 and body["session"]["away_minutes"] == 0
    assert body["session"]["state"] == "away"

    clock.advance(minutes=5)  # these 5 minutes were spent away
    body = _beat(client, employee, beacons=[reading(rssi=-90)]).json()  # still too weak
    assert body["state"] == "away"
    assert body["session"]["present_minutes"] == 5 and body["session"]["away_minutes"] == 5

    clock.advance(minutes=5)  # another 5 away, now back at the desk
    body = _beat(client, employee, beacons=[reading(rssi=-55)]).json()
    assert body["state"] == "present"
    assert body["session"]["present_minutes"] == 5 and body["session"]["away_minutes"] == 10

    clock.advance(minutes=5)  # 5 present
    body = _beat(client, employee, beacons=[reading()]).json()
    assert body["session"]["present_minutes"] == 10 and body["session"]["away_minutes"] == 10
    assert body["session"]["minutes"] == 10

    status = client.get("/api/attendance/status", headers=auth(employee)).json()
    assert status["presence"] == "present"
    assert status["today_minutes"] == 10


def test_open_session_counts_pending_present_tail(client, clock, employee):
    _check_in(client, employee)
    clock.advance(minutes=7)
    status = client.get("/api/attendance/status", headers=auth(employee)).json()
    assert status["session"]["minutes"] == 7 and status["today_minutes"] == 7


def test_grace_cap_when_phone_goes_silent(client, clock, employee):
    _check_in(client, employee)
    clock.advance(hours=3)  # phone dead; no heartbeats
    # while silent the open tail is capped at the grace window
    assert (
        client.get("/api/attendance/status", headers=auth(employee)).json()["today_minutes"] == 15
    )
    body = _beat(client, employee, beacons=[reading()]).json()
    assert body["state"] == "present"
    assert body["session"]["present_minutes"] == 15  # not 180
    assert body["session"]["away_minutes"] == 0


def test_grace_cap_applies_to_away_time_too(client, clock, employee):
    _check_in(client, employee)
    clock.advance(minutes=5)
    _beat(client, employee, beacons=[])  # away
    clock.advance(hours=2)  # silent
    body = _beat(client, employee, beacons=[reading()]).json()
    assert body["session"]["present_minutes"] == 5
    assert body["session"]["away_minutes"] == 15


def test_outside_geofence_auto_checks_out(client, clock, employee):
    _check_in(client, employee)
    clock.advance(minutes=10)
    body = _beat(client, employee, beacons=[reading()], where=OUTSIDE).json()
    assert body["state"] == "outside" and body["checked_in"] is False
    session = body["session"]
    assert session["ended_at"] is not None and session["state"] == "outside"
    assert session["present_minutes"] == 10
    status = client.get("/api/attendance/status", headers=auth(employee)).json()
    assert status["checked_in"] is False and status["presence"] is None
    assert _beat(client, employee).status_code == 409


def test_heartbeat_without_bluetooth_requirement_is_present(client, clock, employee, settings):
    settings.require_bluetooth = False
    _check_in(client, employee)
    clock.advance(minutes=5)
    assert _beat(client, employee, beacons=[]).json()["state"] == "present"


def test_presence_log_is_recorded(client, clock, db, employee):
    _check_in(client, employee)
    clock.advance(minutes=5)
    _beat(client, employee, beacons=[reading(rssi=-61)])
    doc = asyncio.run(db.attendance_sessions.find_one({"employee_id": "EMP0001"}))
    (entry,) = doc["presence_log"]
    assert entry["state"] == "present" and entry["best_rssi"] == -61
    assert 0 < entry["distance_m"] < 150


# ---------- API: check-out and reports ----------


def test_check_out_finalises_last_interval(client, clock, employee):
    _check_in(client, employee)
    clock.advance(minutes=5)
    _beat(client, employee, beacons=[reading()])
    clock.advance(minutes=8)  # 8 more minutes at the desk, then check out
    r = client.post("/api/attendance/check-out", headers=auth(employee), json=INSIDE)
    assert r.status_code == 200
    body = r.json()
    assert body["ended_at"] is not None
    assert body["minutes"] == 13 and body["present_minutes"] == 13

    # closed sessions no longer grow
    clock.advance(hours=1)
    status = client.get("/api/attendance/status", headers=auth(employee)).json()
    assert status["checked_in"] is False and status["today_minutes"] == 13
    assert client.post("/api/attendance/check-out", headers=auth(employee)).status_code == 409


def test_check_out_after_away_credits_away(client, clock, employee):
    _check_in(client, employee)
    clock.advance(minutes=5)
    _beat(client, employee, beacons=[])  # away
    clock.advance(minutes=6)
    body = client.post("/api/attendance/check-out", headers=auth(employee)).json()
    assert body["present_minutes"] == 5 and body["away_minutes"] == 6 and body["minutes"] == 5


def test_check_out_caps_silent_gap(client, clock, employee):
    _check_in(client, employee)
    clock.advance(hours=5)
    body = client.post("/api/attendance/check-out", headers=auth(employee)).json()
    assert body["minutes"] == 15


def test_report_uses_present_time(client, clock, admin_token, office_id, employee):
    _check_in(client, employee)
    clock.advance(minutes=20)
    _beat(client, employee, beacons=[])  # present 15 (grace), 5 min gap dropped
    clock.advance(minutes=10)
    client.post("/api/attendance/check-out", headers=auth(employee))  # 10 away

    report = client.get("/api/attendance/me", headers=auth(employee)).json()
    assert report["total_minutes"] == 15
    (day,) = report["days"]
    assert day["total_minutes"] == 15
    session = day["sessions"][0]
    assert session["present_minutes"] == 15 and session["away_minutes"] == 10
    assert session["source"] == "app"

    # manager/admin reports use the same numbers
    team = client.get("/api/manager/attendance", headers=auth(admin_token)).json()
    alice = next(t for t in team if t["employee_id"] == "EMP0001")
    assert alice["total_minutes"] == 15


def test_request_sessions_count_full_duration(client, admin_token, office_id):
    register(client, office_id, "Manny", "manny@example.com")
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"})
    manager = client.post(
        "/api/auth/login", json={"employee_id": "EMP0001", "password": "password123"}
    ).json()["access_token"]
    emp = register(client, office_id, "Alice", "alice@example.com")["access_token"]
    req = client.post(
        "/api/requests",
        headers=auth(emp),
        json={
            "reason": "Forgot to check in",
            "start_date": "2026-11-02",
            "end_date": "2026-11-02",
            "entry_time": "09:00",
            "exit_time": "10:00",
        },
    ).json()
    client.post(
        f"/api/manager/requests/{req['id']}/decision",
        headers=auth(manager),
        json={"action": "approve"},
    )
    report = client.get(
        "/api/attendance/me?start=2026-11-02&end=2026-11-02", headers=auth(emp)
    ).json()
    session = report["days"][0]["sessions"][0]
    assert session["source"] == "request" and session["state"] is None
    assert session["minutes"] == 60 and session["present_minutes"] == 60
    assert session["away_minutes"] == 0


def test_open_session_is_capped_at_max_hours(client, clock, db, employee):
    _check_in(client, employee)
    asyncio.run(
        db.attendance_sessions.update_one(
            {"employee_id": "EMP0001"}, {"$set": {"present_seconds": 20 * 3600}}
        )
    )
    status = client.get("/api/attendance/status", headers=auth(employee)).json()
    assert status["session"]["minutes"] == Settings(_env_file=None).max_session_hours * 60


def test_legacy_session_without_presence_fields_uses_wall_clock(client, db, office_id):
    token = register(client, office_id)["access_token"]
    start = datetime.now(UTC) - timedelta(hours=2)
    asyncio.run(
        db.attendance_sessions.insert_one(
            {
                "employee_id": "EMP0001",
                "date": start.date().isoformat(),
                "started_at": start,
                "ended_at": start + timedelta(hours=1),
                "source": "app",
            }
        )
    )
    report = client.get("/api/attendance/me", headers=auth(token)).json()
    assert report["total_minutes"] == 60
