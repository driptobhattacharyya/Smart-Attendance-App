"""Contract v3: presence board, dashboard, session detail and CSV export (+ their pure helpers)."""

import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.presence import (
    board_status,
    day_status,
    has_beacon,
    is_late,
    last_heartbeat_rssi,
    latest_app_session,
)

from .conftest import DESK_UUID, ZONE_UUID, auth, login, make_beacon, register

IST = ZoneInfo("Asia/Kolkata")
GRACE = 15 * 60
# Monday 2026-11-09, 10:30 in Kolkata
NOW = datetime(2026, 11, 9, 5, 0, tzinfo=UTC)
TODAY = "2026-11-09"


def at(hour: int, minute: int = 0, day: int = 9) -> datetime:
    """A Kolkata wall-clock time as UTC; ``day`` is the day of November (may go below 1)."""
    local = datetime(2026, 11, 9, hour, minute, tzinfo=IST) + timedelta(days=day - 9)
    return local.astimezone(UTC)


# ---------- pure functions ----------


def _open(last_state="present", heartbeat_ago=60, started=NOW - timedelta(hours=1)):
    return {
        "source": "app",
        "started_at": started,
        "ended_at": None,
        "last_state": last_state,
        "last_heartbeat_at": NOW - timedelta(seconds=heartbeat_ago),
    }


def test_board_status_open_sessions():
    assert board_status(_open("present"), False, NOW, GRACE) == "present"
    assert board_status(_open("away"), False, NOW, GRACE) == "away"
    # exactly at the grace window is still fine; one second more is stale
    assert board_status(_open(heartbeat_ago=GRACE), False, NOW, GRACE) == "present"
    assert board_status(_open(heartbeat_ago=GRACE + 1), False, NOW, GRACE) == "stale"
    assert board_status(_open("away", GRACE + 1), False, NOW, GRACE) == "stale"
    # leave does not hide an employee who is actually checked in
    assert board_status(_open("present"), True, NOW, GRACE) == "present"
    # old data without presence fields
    legacy = {"started_at": NOW - timedelta(minutes=2), "ended_at": None}
    assert board_status(legacy, False, NOW, GRACE) == "present"
    assert board_status({**legacy, "started_at": NOW - timedelta(hours=1)}, False, NOW, GRACE) == (
        "stale"
    )


def test_board_status_not_checked_in():
    assert board_status(None, False, NOW, GRACE) == "not_checked_in"
    assert board_status(None, True, NOW, GRACE) == "on_leave"
    closed = {"started_at": NOW - timedelta(hours=3), "ended_at": NOW, "last_state": "present"}
    assert board_status(closed, False, NOW, GRACE) == "not_checked_in"
    assert board_status(closed, True, NOW, GRACE) == "on_leave"
    left = {**closed, "last_state": "outside"}  # auto-closed after leaving the geofence
    assert board_status(left, False, NOW, GRACE) == "outside"
    assert board_status(left, True, NOW, GRACE) == "on_leave"


def test_day_status():
    assert day_status(True, False) == "present"
    assert day_status(True, True) == "present"
    assert day_status(False, True) == "on_leave"
    assert day_status(False, False) == "not_checked_in"


def test_latest_app_session_prefers_open_and_ignores_requests():
    old_open = {"source": "app", "started_at": NOW - timedelta(hours=5), "ended_at": None}
    closed_later = {"source": "app", "started_at": NOW - timedelta(hours=1), "ended_at": NOW}
    request = {"source": "request", "started_at": NOW, "ended_at": NOW}
    assert latest_app_session([]) is None
    assert latest_app_session([request]) is None
    assert latest_app_session([closed_later, old_open, request]) is old_open
    assert latest_app_session([old_open, closed_later]) is old_open
    older = {"source": "app", "started_at": NOW - timedelta(hours=9), "ended_at": NOW}
    assert latest_app_session([older, closed_later]) is closed_later


def test_is_late_uses_office_time_at_minute_precision():
    start = "09:30"
    assert not is_late(at(9, 29), start, IST)
    assert not is_late(at(9, 30) + timedelta(seconds=45), start, IST)
    assert is_late(at(9, 31), start, IST)
    # 04:01 UTC is 09:31 in Kolkata (late) but only 04:01 in UTC (not late)
    moment = datetime(2026, 11, 9, 4, 1, tzinfo=UTC)
    assert is_late(moment, start, IST)
    assert not is_late(moment, start, ZoneInfo("UTC"))
    assert is_late(at(8, 5), "08:00", IST)
    # naive datetimes (mongomock) are UTC
    assert is_late(moment.replace(tzinfo=None), start, IST)


def test_has_beacon_mirrors_beacons_mine():
    user = {"employee_id": "E1", "office_id": "A"}
    mine = {"assigned_employee_id": "E1", "office_id": "A", "active": True}
    zone = {"assigned_employee_id": None, "office_id": "A"}
    assert has_beacon(user, [mine])
    assert has_beacon(user, [zone])
    assert not has_beacon(user, [])
    assert not has_beacon(user, [{**mine, "active": False}])
    assert not has_beacon(user, [{**zone, "active": False}])
    assert not has_beacon(user, [{**zone, "office_id": "B"}])
    assert not has_beacon(user, [{**mine, "assigned_employee_id": "E2"}])
    assert not has_beacon({"employee_id": "E1"}, [zone])  # no office, nothing assigned


def test_last_heartbeat_rssi():
    assert last_heartbeat_rssi({}) is None
    assert last_heartbeat_rssi({"check_in": {"best_rssi": -70}}) == -70
    log = [{"best_rssi": -60}, {"best_rssi": None}]
    assert last_heartbeat_rssi({"presence_log": log, "check_in": {"best_rssi": -70}}) is None
    assert last_heartbeat_rssi({"presence_log": [{"best_rssi": -61}]}) == -61


# ---------- fixtures ----------


@pytest.fixture
def now(clock):
    clock.now = NOW
    return clock


def add_session(
    db,
    employee_id,
    start,
    end=None,
    *,
    office_id,
    source="app",
    state="present",
    present=0,
    away=0,
    heartbeat=None,
    log=None,
    check_in=None,
    tz=IST,
):
    """Insert a session directly. ``present``/``away`` are minutes."""
    doc = {
        "employee_id": employee_id,
        "team_id": office_id,
        "office_id": office_id,
        "date": start.astimezone(tz).date().isoformat(),
        "started_at": start,
        "ended_at": end,
        "source": source,
    }
    if source == "app":
        doc.update(
            last_heartbeat_at=heartbeat or end or start,
            last_state=state,
            present_seconds=present * 60,
            away_seconds=away * 60,
            presence_log=log or [],
        )
        if check_in:
            doc["check_in"] = check_in
    result = asyncio.run(db.attendance_sessions.insert_one(doc))
    return str(result.inserted_id)


def add_leave(db, employee_id, office_id, start="2026-11-08", end="2026-11-10", status="Approved"):
    asyncio.run(
        db.attendance_requests.insert_one(
            {
                "employee_id": employee_id,
                "team_id": office_id,
                "kind": "leave",
                "status": status,
                "reason": "Family trip",
                "start_date": start,
                "end_date": end,
                "submitted_at": NOW,
            }
        )
    )


def set_user(db, employee_id, **fields):
    asyncio.run(db.users.update_one({"employee_id": employee_id}, {"$set": fields}))


def other_office(client, admin_token, name="Other"):
    r = client.post(
        "/api/offices",
        headers=auth(admin_token),
        json={"name": name, "latitude": 1, "longitude": 1, "radius": 100, "timezone": "UTC"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def board(client, db, admin_token, office_id, now):
    """Team A (Kolkata): manager Manny EMP0001, then Ananya (present), Bala (away), Chitra (stale),
    Dev (outside), Esha (not checked in), Farid (on leave), Gita (inactive) and the admin, who
    is also in team A. Team B has Zed, who is present."""
    register(client, office_id, "Manny", "manny@example.com")
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"})
    people = {}
    for name in ("Ananya", "Bala", "Chitra", "Dev", "Esha", "Farid", "Gita"):
        body = register(client, office_id, name, f"{name.lower()}@example.com")
        people[name] = body["employee_id"]
    other = other_office(client, admin_token)
    people["Zed"] = register(client, other, "Zed", "zed@example.com")["employee_id"]
    set_user(db, people["Gita"], active=False)

    kw = {"office_id": office_id}
    add_session(
        db,
        people["Ananya"],
        at(9, 0),
        office_id=office_id,
        present=60,
        heartbeat=NOW - timedelta(minutes=2),
        log=[
            {
                "at": NOW - timedelta(minutes=7),
                "state": "present",
                "distance_m": 9,
                "best_rssi": -70,
            },
            {
                "at": NOW - timedelta(minutes=2),
                "state": "present",
                "distance_m": 8,
                "best_rssi": -58,
            },
        ],
        check_in={"latitude": 22.5, "longitude": 88.3, "distance_m": 12.3, "best_rssi": -60},
    )
    add_session(
        db, people["Bala"], at(9, 30), state="away", heartbeat=NOW - timedelta(minutes=5), **kw
    )
    add_session(
        db,
        people["Chitra"],
        at(9, 0),
        present=30,
        heartbeat=NOW - timedelta(minutes=20),
        check_in={"latitude": 22.5, "longitude": 88.3, "distance_m": 5.0, "best_rssi": -66},
        **kw,
    )
    add_session(db, people["Dev"], at(9, 0), at(9, 40), state="outside", present=30, **kw)
    add_leave(db, people["Farid"], office_id)
    add_session(
        db, people["Zed"], NOW - timedelta(hours=1), office_id=other, heartbeat=NOW, present=10
    )
    people["_other_office"] = other
    return people


def manager_token(client):
    return login(client, "EMP0001", "password123")


# ---------- presence board ----------


def test_presence_board_rows_and_order(client, board, now):
    r = client.get("/api/manager/presence", headers=auth(manager_token(client)))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [(x["name"], x["status"]) for x in rows] == [
        ("Ananya", "present"),
        ("Bala", "away"),
        ("Chitra", "stale"),
        ("Dev", "outside"),
        ("Admin", "not_checked_in"),
        ("Esha", "not_checked_in"),
        ("Manny", "not_checked_in"),
        ("Farid", "on_leave"),
    ]
    by_name = {x["name"]: x for x in rows}
    ananya = by_name["Ananya"]
    assert ananya["employee_id"] == board["Ananya"]
    assert ananya["checked_in"] is True
    assert ananya["session_id"]
    assert ananya["since"].startswith("2026-11-09T03:30:00")
    assert ananya["last_heartbeat_at"].startswith("2026-11-09T04:58:00")
    assert ananya["best_rssi"] == -58  # newest presence-log entry
    # 60 present minutes credited + 2 minutes since the last heartbeat
    assert ananya["today_minutes"] == 62
    assert set(ananya) == {
        "employee_id", "name", "office_id", "status", "checked_in", "session_id", "since",
        "last_heartbeat_at", "best_rssi", "today_minutes", "face_enrolled", "has_beacon",
    }  # fmt: skip

    chitra = by_name["Chitra"]
    assert chitra["checked_in"] is True and chitra["best_rssi"] == -66  # falls back to check-in
    assert by_name["Bala"]["best_rssi"] is None
    dev = by_name["Dev"]
    assert dev["checked_in"] is False
    assert dev["session_id"] is None and dev["since"] is None and dev["last_heartbeat_at"] is None
    assert dev["best_rssi"] is None and dev["today_minutes"] == 30
    assert by_name["Esha"]["today_minutes"] == 0
    assert by_name["Farid"]["checked_in"] is False
    # inactive users and other teams are not on the board
    assert "Gita" not in by_name and "Zed" not in by_name


def test_presence_scoping_admin_and_employee(client, board, admin_token, office_id, now):
    admin_rows = client.get("/api/manager/presence", headers=auth(admin_token)).json()
    names = {x["name"] for x in admin_rows}
    assert "Zed" in names and "Ananya" in names and "Gita" not in names
    assert next(x for x in admin_rows if x["name"] == "Zed")["status"] == "present"

    manager = manager_token(client)
    # office_id filter, including an office outside the manager's team
    only_b = client.get(
        "/api/manager/presence", params={"office_id": board["_other_office"]}, headers=auth(manager)
    )
    assert only_b.status_code == 200 and only_b.json() == []
    only_b = client.get(
        "/api/manager/presence",
        params={"office_id": board["_other_office"]},
        headers=auth(admin_token),
    )
    assert [x["name"] for x in only_b.json()] == ["Zed"]
    only_a = client.get(
        "/api/manager/presence", params={"office_id": office_id}, headers=auth(manager)
    )
    assert len(only_a.json()) == 8

    employee = login(client, board["Ananya"], "password123")
    assert client.get("/api/manager/presence", headers=auth(employee)).status_code == 403
    assert client.get("/api/manager/presence").status_code == 401


def test_presence_has_beacon_and_face_flags(client, db, board, admin_token, office_id, now):
    manager = manager_token(client)
    make_beacon(client, admin_token, office_id, assigned_employee_id=board["Ananya"])
    make_beacon(
        client, admin_token, office_id, uuid=ZONE_UUID, assigned_employee_id=board["Bala"],
        active=False,
    )  # fmt: skip
    set_user(db, board["Ananya"], face_embedding=[0.1, 0.2])
    set_user(db, board["Bala"], azure_person_id="p-1")
    rows = {x["name"]: x for x in client.get("/api/manager/presence", headers=auth(manager)).json()}
    assert rows["Ananya"]["has_beacon"] is True and rows["Ananya"]["face_enrolled"] is True
    assert rows["Bala"]["has_beacon"] is False and rows["Bala"]["face_enrolled"] is True
    assert rows["Esha"]["face_enrolled"] is False

    # a shared zone beacon makes everyone in the office reachable
    make_beacon(client, admin_token, office_id, name="Zone", uuid=ZONE_UUID, major=None, minor=None)
    rows = {x["name"]: x for x in client.get("/api/manager/presence", headers=auth(manager)).json()}
    assert rows["Esha"]["has_beacon"] is True and rows["Bala"]["has_beacon"] is True
    # ...but not the other office's employee, which only the admin can see
    admin_rows = client.get("/api/manager/presence", headers=auth(admin_token)).json()
    assert next(x for x in admin_rows if x["name"] == "Zed")["has_beacon"] is False


def test_presence_yesterdays_open_session_and_old_sessions(client, db, board, office_id, now):
    # Esha forgot to check out yesterday and the phone never reported again: stale, not "absent"
    add_session(db, board["Esha"], at(9, 0, day=8), office_id=office_id, heartbeat=at(9, 5, day=8))
    # Esha's session from last week is closed and must not matter
    rows = {
        x["name"]: x
        for x in client.get("/api/manager/presence", headers=auth(manager_token(client))).json()
    }
    assert rows["Esha"]["status"] == "stale" and rows["Esha"]["checked_in"] is True
    assert rows["Esha"]["today_minutes"] == 0


def test_presence_uses_each_employees_office_timezone(client, db, admin_token, office_id, now):
    # 20:00 UTC on the 8th is already the 9th in Kolkata but still the 8th in UTC
    utc_office = other_office(client, admin_token)
    emp = register(client, utc_office, "Una", "una@example.com")["employee_id"]
    now.now = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)
    add_session(
        db,
        emp,
        datetime(2026, 11, 8, 19, 0, tzinfo=UTC),
        datetime(2026, 11, 8, 19, 30, tzinfo=UTC),
        office_id=utc_office,
        present=30,
        tz=UTC,
    )
    rows = client.get("/api/manager/presence", headers=auth(admin_token)).json()
    assert next(x for x in rows if x["name"] == "Una")["today_minutes"] == 30


# ---------- dashboard ----------


def test_dashboard_counts_manager(client, db, board, admin_token, office_id, now):
    manager = manager_token(client)
    set_user(db, board["Ananya"], face_embedding=[0.1])
    set_user(db, board["Bala"], face_embedding=[0.1])
    # pending requests: one in team A, one in team B, one already decided
    for eid, team, state in (
        (board["Esha"], office_id, "Pending"),
        (board["Zed"], board["_other_office"], "Pending"),
        (board["Dev"], office_id, "Approved"),
    ):
        asyncio.run(
            db.attendance_requests.insert_one(
                {
                    "employee_id": eid,
                    "team_id": team,
                    "kind": "correction",
                    "status": state,
                    "reason": "x",
                    "start_date": TODAY,
                    "end_date": TODAY,
                    "submitted_at": NOW,
                }
            )  # fmt: skip
        )
    for office, assigned in ((office_id, None), (board["_other_office"], None), (office_id, "E")):
        asyncio.run(
            db.beacons.insert_one(
                {
                    "office_id": office,
                    "name": "b",
                    "uuid": DESK_UUID,
                    "major": 1,
                    "minor": 1,
                    "assigned_employee_id": assigned,
                    "active": True,
                }
            )  # fmt: skip
        )
    asyncio.run(
        db.beacons.insert_one(
            {
                "office_id": office_id,
                "name": "off",
                "uuid": DESK_UUID,
                "major": 2,
                "minor": 2,
                "assigned_employee_id": None,
                "active": False,
            }
        )  # fmt: skip
    )

    r = client.get("/api/manager/dashboard", headers=auth(manager))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == TODAY
    assert body["headcount"] == {
        "total": 8, "present": 1, "away": 1, "stale": 1, "outside": 1,
        "not_checked_in": 3, "on_leave": 1,
    }  # fmt: skip
    assert body["pending_requests"] == 1  # only team A's
    assert body["unassigned_beacons"] is None  # admins only
    assert body["employees_without_face"] == 6  # 8 active members, 2 enrolled
    assert body["employees_without_beacon"] == 0  # REQUIRE_BLUETOOTH is off in this fixture

    admin = client.get("/api/manager/dashboard", headers=auth(admin_token)).json()
    assert admin["headcount"]["total"] == 9 and admin["headcount"]["present"] == 2
    assert admin["pending_requests"] == 2
    assert admin["unassigned_beacons"] == 2  # active and unassigned; not the assigned or inactive


def test_dashboard_without_beacon_when_bluetooth_required(client, board, settings, now):
    settings.require_bluetooth = True
    body = client.get("/api/manager/dashboard", headers=auth(manager_token(client))).json()
    assert body["employees_without_beacon"] == 8


def test_dashboard_daily_totals(client, db, board, office_id, now):
    manager = manager_token(client)
    # Today (open Ananya, stale Chitra...) comes from the board fixture. Add history:
    d = lambda n: 9 - n  # noqa: E731 - day-of-month n days ago
    add_session(db, board["Ananya"], at(9, 0, d(1)), at(17, 0, d(1)), office_id=office_id,
                present=420, away=30)  # fmt: skip
    add_session(db, board["Bala"], at(10, 0, d(1)), at(12, 0, d(1)), office_id=office_id,
                present=100, away=20)  # fmt: skip
    add_session(db, board["Bala"], at(13, 0, d(1)), at(14, 0, d(1)), office_id=office_id,
                present=60)  # fmt: skip
    add_session(db, board["Ananya"], at(9, 0, d(13)), at(10, 0, d(13)), office_id=office_id,
                present=60)  # fmt: skip
    add_session(db, board["Ananya"], at(9, 0, d(14)), at(10, 0, d(14)), office_id=office_id,
                present=999)  # fmt: skip  # outside the 14-day window
    add_session(db, board["Zed"], at(9, 0, d(1)), at(10, 0, d(1)), office_id=board["_other_office"],
                present=500)  # fmt: skip  # other team
    add_session(db, board["Farid"], at(9, 0, d(2)), at(10, 0, d(2)), office_id=office_id,
                source="request")  # fmt: skip

    daily = client.get("/api/manager/dashboard", headers=auth(manager)).json()["daily"]
    assert len(daily) == 14
    assert daily[0]["date"] == "2026-10-27" and daily[-1]["date"] == TODAY
    assert [x["date"] for x in daily] == sorted(x["date"] for x in daily)
    by_date = {x["date"]: x for x in daily}
    assert by_date["2026-11-08"] == {
        "date": "2026-11-08", "worked_minutes": 580, "away_minutes": 50, "people": 2,
    }  # fmt: skip
    assert by_date["2026-10-27"]["worked_minutes"] == 60 and by_date["2026-10-27"]["people"] == 1
    assert by_date["2026-11-07"] == {
        "date": "2026-11-07",
        "worked_minutes": 60,
        "away_minutes": 0,
        "people": 1,
    }  # request sessions count as worked time too
    assert by_date["2026-11-05"]["worked_minutes"] == 0
    assert by_date[TODAY]["worked_minutes"] > 0

    # the admin sees Zed's day as well
    admin_daily = client.get("/api/manager/dashboard", headers=auth(manager_token(client))).json()[
        "daily"
    ]
    assert admin_daily == daily


def test_dashboard_late_today_uses_office_timezone_and_work_start(
    client, db, admin_token, office_id, now
):
    names = ["Ontime", "Late1", "Late2", "Edge"]
    ids = {}
    register(client, office_id, "Manny", "manny@example.com")
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"})
    for n in names:
        ids[n] = register(client, office_id, n, f"{n.lower()}@example.com")["employee_id"]
    # 09:29 IST (= 03:59 UTC) is on time; 09:40 IST is late; 14:30 IST is "9:00 UTC" and late
    add_session(db, ids["Ontime"], at(9, 29), at(10, 0), office_id=office_id)
    add_session(db, ids["Late1"], at(9, 40), at(10, 0), office_id=office_id)
    add_session(db, ids["Late2"], at(14, 30), office_id=office_id, heartbeat=NOW)
    add_session(db, ids["Edge"], at(9, 30), at(10, 0), office_id=office_id)
    # second session: only the first check-in of the day matters
    add_session(db, ids["Ontime"], at(11, 0), at(12, 0), office_id=office_id)
    manager = manager_token(client)

    late = client.get("/api/manager/dashboard", headers=auth(manager)).json()["late_today"]
    assert [(x["name"], x["employee_id"]) for x in late] == [
        ("Late2", ids["Late2"]),
        ("Late1", ids["Late1"]),
    ]  # latest arrival first
    assert late[1]["first_check_in"].startswith("2026-11-09T04:10:00")

    # a different work start moves the line
    r = client.patch(
        f"/api/offices/{office_id}", headers=auth(admin_token), json={"work_start": "09:00"}
    )
    assert r.status_code == 200 and r.json()["work_start"] == "09:00"
    late = client.get("/api/manager/dashboard", headers=auth(manager)).json()["late_today"]
    assert {x["name"] for x in late} == {"Late1", "Late2", "Edge", "Ontime"}
    r = client.patch(
        f"/api/offices/{office_id}", headers=auth(admin_token), json={"work_start": "10:00"}
    )
    late = client.get("/api/manager/dashboard", headers=auth(manager)).json()["late_today"]
    assert [x["name"] for x in late] == ["Late2"]


def test_dashboard_late_today_is_capped_at_ten(client, db, admin_token, office_id, now):
    register(client, office_id, "Manny", "manny@example.com")
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"})
    for i in range(12):
        eid = register(client, office_id, f"P{i:02}", f"p{i}@example.com")["employee_id"]
        add_session(db, eid, at(10, i), at(11, 0), office_id=office_id)
    late = client.get("/api/manager/dashboard", headers=auth(manager_token(client))).json()
    assert len(late["late_today"]) == 10
    assert late["late_today"][0]["name"] == "P11"  # the latest arrivals are the ones listed


def test_dashboard_date_param_and_default_in_office_timezone(client, db, board, office_id, now):
    manager = manager_token(client)
    # 20:00 UTC on the 8th is already the 9th in Kolkata
    now.now = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)
    assert client.get("/api/manager/dashboard", headers=auth(manager)).json()["date"] == TODAY

    # A past date has no "live" states: whoever worked that day counts as present
    add_session(db, board["Esha"], at(9, 0, 5), at(17, 0, 5), office_id=office_id, present=480)
    add_leave(db, board["Farid"], office_id, "2026-11-05", "2026-11-05")
    body = client.get(
        "/api/manager/dashboard", params={"date": "2026-11-05"}, headers=auth(manager)
    ).json()
    assert body["date"] == "2026-11-05"
    assert body["headcount"]["present"] == 1
    assert body["headcount"]["on_leave"] == 1
    assert body["headcount"]["total"] == 8
    assert body["headcount"]["away"] == body["headcount"]["stale"] == 0
    assert body["daily"][-1]["date"] == "2026-11-05" and body["daily"][-1]["worked_minutes"] == 480
    assert body["late_today"] == []
    assert (
        client.get("/api/manager/dashboard", params={"date": "nope"}, headers=auth(manager))
    ).status_code == 422


def test_dashboard_empty_team_and_permissions(client, admin_token, office_id):
    body = client.get("/api/manager/dashboard", headers=auth(admin_token)).json()
    assert body["headcount"]["total"] == 1  # just the admin
    assert len(body["daily"]) == 14 and body["late_today"] == []
    employee = register(client, office_id)["access_token"]
    assert client.get("/api/manager/dashboard", headers=auth(employee)).status_code == 403


# ---------- office work_start ----------


def test_office_work_start_validation(client, admin_token):
    offices = client.get("/api/offices").json()
    created = client.post(
        "/api/offices",
        headers=auth(admin_token),
        json={"name": "N", "latitude": 1, "longitude": 1, "radius": 100},
    ).json()
    assert created["work_start"] == "09:30"  # default
    assert all(o["work_start"] == "09:30" for o in offices)  # offices created before the field
    ok = client.post(
        "/api/offices",
        headers=auth(admin_token),
        json={"name": "M", "latitude": 1, "longitude": 1, "radius": 100, "work_start": "08:15"},
    )
    assert ok.status_code == 201 and ok.json()["work_start"] == "08:15"
    for bad in ("9:30", "25:00", "09:30:15", "noon", "", "09:60"):
        r = client.post(
            "/api/offices",
            headers=auth(admin_token),
            json={"name": "X", "latitude": 1, "longitude": 1, "radius": 100, "work_start": bad},
        )
        assert r.status_code == 422, bad
    for bad in (None, "7"):
        r = client.patch(
            f"/api/offices/{created['id']}", headers=auth(admin_token), json={"work_start": bad}
        )
        assert r.status_code == 422, bad
    r = client.patch(
        f"/api/offices/{created['id']}", headers=auth(admin_token), json={"name": "N2"}
    )
    assert r.json()["work_start"] == "09:30"  # untouched


# ---------- session detail ----------


def test_session_detail_with_timeline(client, db, board, office_id, now):
    manager = manager_token(client)
    sid = next(
        x["session_id"]
        for x in client.get("/api/manager/presence", headers=auth(manager)).json()
        if x["name"] == "Ananya"
    )
    r = client.get(f"/api/manager/sessions/{sid}", headers=auth(manager))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == sid
    assert body["employee_id"] == board["Ananya"] and body["name"] == "Ananya"
    assert body["office_id"] == office_id
    assert body["date"] == TODAY and body["source"] == "app" and body["state"] == "present"
    assert body["minutes"] == body["present_minutes"] == 62
    assert body["check_in"] == {
        "latitude": 22.5, "longitude": 88.3, "distance_m": 12.3, "best_rssi": -60,
    }  # fmt: skip
    assert [(e["state"], e["distance_m"], e["best_rssi"]) for e in body["presence_log"]] == [
        ("present", 9, -70),
        ("present", 8, -58),
    ]
    assert body["presence_log"][0]["at"].startswith("2026-11-09T04:53:00")


def test_session_detail_request_and_pruned_sessions(client, db, board, office_id, now):
    manager = manager_token(client)
    sid = add_session(db, board["Esha"], at(9, 0, 3), at(17, 0, 3), office_id=office_id,
                      source="request")  # fmt: skip
    body = client.get(f"/api/manager/sessions/{sid}", headers=auth(manager)).json()
    assert body["source"] == "request" and body["state"] is None and body["minutes"] == 480
    assert body["check_in"] is None and body["presence_log"] == []
    # sessions without check-in data (pruned) still load
    sid = add_session(db, board["Esha"], at(9, 0, 2), at(10, 0, 2), office_id=office_id, present=60)
    body = client.get(f"/api/manager/sessions/{sid}", headers=auth(manager)).json()
    assert body["check_in"] is None and body["presence_log"] == [] and body["minutes"] == 60


def test_session_detail_is_team_scoped(client, db, board, admin_token, now):
    manager = manager_token(client)
    zed_session = asyncio.run(db.attendance_sessions.find_one({"employee_id": board["Zed"]}))
    sid = str(zed_session["_id"])
    assert client.get(f"/api/manager/sessions/{sid}", headers=auth(manager)).status_code == 404
    assert client.get(f"/api/manager/sessions/{sid}", headers=auth(admin_token)).status_code == 200
    # unknown / malformed ids
    assert (
        client.get("/api/manager/sessions/000000000000000000000000", headers=auth(manager))
    ).status_code == 404
    assert client.get("/api/manager/sessions/not-an-id", headers=auth(manager)).status_code == 404
    employee = login(client, board["Ananya"], "password123")
    assert client.get(f"/api/manager/sessions/{sid}", headers=auth(employee)).status_code == 403


def test_day_detail_sessions_carry_ids_for_the_timeline(client, board, office_id, now):
    manager = manager_token(client)
    r = client.get(
        f"/api/manager/attendance/{board['Ananya']}",
        params={"start": TODAY, "end": TODAY},
        headers=auth(manager),
    )
    sid = r.json()["days"][0]["sessions"][0]["id"]
    assert client.get(f"/api/manager/sessions/{sid}", headers=auth(manager)).status_code == 200


# ---------- CSV export ----------


def _csv(client, token, **params):
    params = {"start": "2026-11-01", "end": "2026-11-30", **params}
    return client.get("/api/manager/attendance/export.csv", params=params, headers=auth(token))


def _rows(response):
    return list(csv.DictReader(io.StringIO(response.text)))


def test_export_csv_rows_and_format(client, db, board, office_id, now):
    manager = manager_token(client)
    # Ananya: two sessions on the 6th; on the 9th (today) she is still open; Farid on leave 8-10
    add_session(db, board["Ananya"], at(9, 12, 6), at(12, 0, 6), office_id=office_id,
                present=150, away=18)  # fmt: skip
    add_session(db, board["Ananya"], at(13, 0, 6), at(17, 45, 6), office_id=office_id,
                present=280, away=5)  # fmt: skip
    r = _csv(client, manager)
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"] == (
        'attachment; filename="attendance_2026-11-01_2026-11-30.csv"'
    )
    lines = r.text.splitlines()
    assert lines[0] == (
        "employee_id,name,office,date,first_in,last_out,worked_minutes,away_minutes,sessions,"
        "on_leave"
    )
    rows = _rows(r)
    ananya_6th = next(
        x for x in rows if x["employee_id"] == board["Ananya"] and x["date"] == "2026-11-06"
    )
    assert ananya_6th == {
        "employee_id": board["Ananya"], "name": "Ananya", "office": "HQ", "date": "2026-11-06",
        "first_in": "09:12", "last_out": "17:45", "worked_minutes": "430", "away_minutes": "23",
        "sessions": "2", "on_leave": "false",
    }  # fmt: skip
    # open session today: no last_out yet
    ananya_today = next(
        x for x in rows if x["employee_id"] == board["Ananya"] and x["date"] == TODAY
    )
    assert ananya_today["first_in"] == "09:00" and ananya_today["last_out"] == ""
    assert ananya_today["sessions"] == "1"
    # leave-only rows: one per leave day, no times
    farid = [x for x in rows if x["employee_id"] == board["Farid"]]
    assert [x["date"] for x in farid] == ["2026-11-08", "2026-11-09", "2026-11-10"]
    assert all(
        x["on_leave"] == "true" and x["sessions"] == "0" and x["first_in"] == "" for x in farid
    )
    assert all(x["worked_minutes"] == "0" for x in farid)
    # employees with nothing in range have no rows; ordering is by employee then date
    assert not any(x["employee_id"] == board["Esha"] for x in rows)
    keys = [(x["employee_id"], x["date"]) for x in rows]
    assert keys == sorted(keys)


def test_export_csv_escaping(client, db, board, office_id, now):
    manager = manager_token(client)
    nasty = 'Smith, "Bob" Jr.'
    set_user(db, board["Esha"], name=nasty)
    set_user(db, board["Dev"], name='=HYPERLINK("http://evil")')
    add_session(db, board["Esha"], at(9, 0, 2), at(10, 0, 2), office_id=office_id, present=60)
    r = _csv(client, manager)
    assert '"Smith, ""Bob"" Jr."' in r.text  # properly quoted at the wire level
    rows = _rows(r)
    esha = next(x for x in rows if x["employee_id"] == board["Esha"])
    assert esha["name"] == nasty  # and round-trips through a CSV parser
    assert len([x for x in rows if x["employee_id"] == board["Esha"]]) == 1
    dev = next(x for x in rows if x["employee_id"] == board["Dev"])
    assert dev["name"].startswith("'=")  # no spreadsheet formula injection


def test_export_csv_is_team_scoped_and_office_filtered(client, board, admin_token, office_id, now):
    manager = manager_token(client)
    ids = {x["employee_id"] for x in _rows(_csv(client, manager))}
    assert board["Zed"] not in ids and board["Ananya"] in ids
    assert board["Gita"] not in ids  # inactive

    admin_rows = _rows(_csv(client, admin_token))
    assert board["Zed"] in {x["employee_id"] for x in admin_rows}
    only_b = _rows(_csv(client, admin_token, office_id=board["_other_office"]))
    assert {x["employee_id"] for x in only_b} == {board["Zed"]}
    assert next(iter(only_b))["office"] == "Other"
    # a manager asking for another team's office gets nothing, not an error
    assert _rows(_csv(client, manager, office_id=board["_other_office"])) == []


def test_export_csv_uses_office_local_times(client, db, admin_token, board, now):
    utc_office = board["_other_office"]  # timezone UTC
    add_session(
        db, board["Zed"], datetime(2026, 11, 3, 9, 5, tzinfo=UTC),
        datetime(2026, 11, 3, 17, 20, tzinfo=UTC), office_id=utc_office, present=480,
    )  # fmt: skip
    row = next(
        x
        for x in _rows(_csv(client, admin_token, office_id=utc_office))
        if x["date"] == "2026-11-03"
    )
    assert (row["first_in"], row["last_out"]) == ("09:05", "17:20")


def test_export_csv_validation_and_auth(client, board, now):
    manager = manager_token(client)
    # route order: "export.csv" must not be captured by /attendance/{employee_id}
    r = _csv(client, manager)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert _csv(client, manager, start="2026-11-30", end="2026-11-01").status_code == 422
    assert _csv(client, manager, start="2026-01-01", end="2026-04-30").status_code == 422
    assert _csv(client, manager, start="2026-08-01", end="2026-11-01").status_code == 200  # 93 days
    r = client.get("/api/manager/attendance/export.csv", headers=auth(manager))
    assert r.status_code == 422  # start and end are required
    employee = login(client, board["Ananya"], "password123")
    assert _csv(client, employee).status_code == 403
    assert client.get("/api/manager/attendance/export.csv").status_code == 401
    # the per-employee route still works
    assert (
        client.get(f"/api/manager/attendance/{board['Ananya']}", headers=auth(manager)).status_code
        == 200
    )


def test_export_csv_empty_range_has_only_header(client, admin_token):
    r = _csv(client, admin_token, start="2020-01-01", end="2020-01-31")
    assert r.status_code == 200 and len(r.text.splitlines()) == 1
