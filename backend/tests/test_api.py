import asyncio
from datetime import timedelta

from app.db import utcnow
from app.geo import haversine_m

from .conftest import INSIDE, OUTSIDE, auth, enroll, login, register, verify


def test_health(client):
    r = client.get("/api/health")
    assert r.json() == {"status": "ok", "face_recognition": True}


def test_haversine():
    # Kolkata -> Delhi is roughly 1,300 km
    assert 1_290_000 < haversine_m(22.5726, 88.3639, 28.6139, 77.2090) < 1_320_000


# ---------- auth ----------


def test_register_and_login(client, office_id):
    body = register(client, office_id)
    assert body["employee_id"] == "EMP0001"
    assert body["office"]["id"] == office_id
    assert body["user"]["role"] == "employee"
    assert body["face_enrolled"] is False

    # login by employee id or email
    login(client, "EMP0001", "password123")
    login(client, "ALICE@example.com", "password123")
    r = client.post("/api/auth/login", json={"employee_id": "EMP0001", "password": "wrong"})
    assert r.status_code == 401


def test_register_rejects_duplicates_and_unknown_office(client, office_id):
    register(client, office_id)
    r = client.post(
        "/api/auth/register",
        json={
            "name": "A2",
            "email": "alice@example.com",
            "password": "password123",
            "office_id": office_id,
        },
    )
    assert r.status_code == 409
    r = client.post(
        "/api/auth/register",
        json={
            "name": "B",
            "email": "b@example.com",
            "password": "password123",
            "office_id": "000000000000000000000000",
        },
    )
    assert r.status_code == 422


def test_protected_routes_need_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/attendance/status").status_code == 401
    r = client.get("/api/auth/me", headers=auth("not-a-jwt"))
    assert r.status_code == 401


def test_employee_cannot_use_manager_or_admin_routes(client, office_id):
    token = register(client, office_id)["access_token"]
    assert client.get("/api/manager/team", headers=auth(token)).status_code == 403
    assert client.get("/api/admin/users", headers=auth(token)).status_code == 403
    r = client.post(
        "/api/offices",
        headers=auth(token),
        json={"name": "X", "latitude": 0, "longitude": 0, "radius": 10},
    )
    assert r.status_code == 403


# ---------- offices ----------


def test_offices_public_list_and_admin_create(client, admin_token):
    assert len(client.get("/api/offices").json()) == 1
    r = client.post(
        "/api/offices",
        headers=auth(admin_token),
        json={
            "name": "Branch",
            "latitude": 12.97,
            "longitude": 77.59,
            "radius": 200,
            "timezone": "Asia/Kolkata",
        },
    )
    assert r.status_code == 201
    r = client.patch(
        f"/api/offices/{r.json()['id']}", headers=auth(admin_token), json={"timezone": "Not/AZone"}
    )
    assert r.status_code == 422
    assert len(client.get("/api/offices").json()) == 2


# ---------- face ----------


def test_face_enroll_and_verify(client, office_id):
    token = register(client, office_id)["access_token"]
    assert verify(client, token).status_code == 409  # nothing enrolled yet

    r = client.post(
        "/api/face/enroll",
        headers=auth(token),
        files={"image": ("f.jpg", b"a photo of a wall", "image/jpeg")},
    )
    assert r.status_code == 422 and "No face" in r.json()["detail"]

    enroll(client, token)
    assert client.get("/api/auth/me", headers=auth(token)).json()["face_enrolled"] is True
    # can't overwrite an enrolled face
    r = client.post(
        "/api/face/enroll",
        headers=auth(token),
        files={"image": ("f.jpg", b"face:mallory", "image/jpeg")},
    )
    assert r.status_code == 409

    good = verify(client, token).json()
    assert good["verified"] is True and good["valid_until"]
    bad = verify(client, token, "mallory").json()
    assert bad["verified"] is False


# ---------- attendance ----------


def _ready_employee(client, office_id, name="Alice", email="alice@example.com", who="alice"):
    token = register(client, office_id, name, email)["access_token"]
    enroll(client, token, who)
    return token


def test_check_in_requires_face_and_geofence(client, office_id):
    token = _ready_employee(client, office_id)

    r = client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE)
    assert r.status_code == 403 and "face" in r.json()["detail"]

    assert verify(client, token).json()["verified"]
    r = client.post("/api/attendance/check-in", headers=auth(token), json=OUTSIDE)
    assert r.status_code == 403 and "m from HQ" in r.json()["detail"]

    r = client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE)
    assert r.status_code == 201, r.text
    assert r.json()["ended_at"] is None

    status = client.get("/api/attendance/status", headers=auth(token)).json()
    assert status["checked_in"] is True
    # the face scan was used up
    assert status["face_verified_until"] is None

    verify(client, token)
    r = client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE)
    assert r.status_code == 409


def test_expired_face_verification(client, db, office_id):
    token = _ready_employee(client, office_id)
    verify(client, token)
    asyncio.run(
        db.users.update_one(
            {"employee_id": "EMP0001"},
            {"$set": {"face_verified_at": utcnow() - timedelta(minutes=30)}},
        )
    )
    r = client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE)
    assert r.status_code == 403


def test_check_out_and_report_totals(client, clock, office_id):
    token = _ready_employee(client, office_id)
    assert client.post("/api/attendance/check-out", headers=auth(token)).status_code == 409

    verify(client, token)
    client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE)
    for _ in range(4):  # a heartbeat every 10 minutes for 40 minutes
        clock.advance(minutes=10)
        r = client.post("/api/attendance/heartbeat", headers=auth(token), json=INSIDE)
        assert r.json()["state"] == "present"
    clock.advance(minutes=5)

    r = client.post("/api/attendance/check-out", headers=auth(token), json=INSIDE)
    assert r.status_code == 200
    assert r.json()["minutes"] == 45

    report = client.get("/api/attendance/me", headers=auth(token)).json()
    assert report["total_minutes"] == 45
    assert len(report["days"]) == 1


def test_report_range_validation(client, office_id):
    token = register(client, office_id)["access_token"]
    r = client.get("/api/attendance/me?start=2026-01-10&end=2026-01-01", headers=auth(token))
    assert r.status_code == 422
    r = client.get("/api/attendance/me?start=2025-01-01&end=2026-01-01", headers=auth(token))
    assert r.status_code == 422


# ---------- requests & manager ----------


def _make_manager(client, admin_token, office_id):
    register(client, office_id, "Manny", "manny@example.com")
    r = client.patch(
        "/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"}
    )
    assert r.status_code == 200 and r.json()["role"] == "manager"
    return login(client, "EMP0001", "password123")


def test_request_validation(client, office_id):
    token = register(client, office_id)["access_token"]
    for bad in [
        {"reason": "x" * 5, "start_date": "2026-01-05", "end_date": "2026-01-01"},
        {
            "reason": "xxxxx",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "entry_time": "09:00",
            "exit_time": "17:00",
        },
        {
            "reason": "xxxxx",
            "start_date": "2026-01-01",
            "end_date": "2026-01-01",
            "entry_time": "18:00",
            "exit_time": "09:00",
        },
        {
            "reason": "xxxxx",
            "start_date": "2026-01-01",
            "end_date": "2026-01-01",
            "entry_time": "09:00",
        },
    ]:
        assert client.post("/api/requests", headers=auth(token), json=bad).status_code == 422


def test_correction_and_leave_approval_flow(client, admin_token, office_id):
    manager = _make_manager(client, admin_token, office_id)
    emp = register(client, office_id, "Alice", "alice@example.com")["access_token"]

    correction = client.post(
        "/api/requests",
        headers=auth(emp),
        json={
            "reason": "Forgot to check in",
            "start_date": "2026-11-02",
            "end_date": "2026-11-02",
            "entry_time": "09:30",
            "exit_time": "18:00",
        },
    ).json()
    assert correction["kind"] == "correction" and correction["status"] == "Pending"
    leave = client.post(
        "/api/requests",
        headers=auth(emp),
        json={"reason": "Family trip", "start_date": "2026-11-04", "end_date": "2026-11-06"},
    ).json()
    assert leave["kind"] == "leave"

    pending = client.get("/api/manager/requests?status=Pending", headers=auth(manager)).json()
    assert {p["id"] for p in pending} == {correction["id"], leave["id"]}
    assert pending[0]["employee_name"] == "Alice"

    r = client.post(
        f"/api/manager/requests/{correction['id']}/decision",
        headers=auth(manager),
        json={"action": "approve"},
    )
    assert r.status_code == 200 and r.json()["status"] == "Approved"
    r = client.post(
        f"/api/manager/requests/{correction['id']}/decision",
        headers=auth(manager),
        json={"action": "reject"},
    )
    assert r.status_code == 409
    client.post(
        f"/api/manager/requests/{leave['id']}/decision",
        headers=auth(manager),
        json={"action": "approve"},
    )

    report = client.get(
        "/api/attendance/me?start=2026-11-01&end=2026-11-07", headers=auth(emp)
    ).json()
    days = {d["date"]: d for d in report["days"]}
    assert days["2026-11-02"]["total_minutes"] == 510  # 09:30 -> 18:00
    assert days["2026-11-02"]["sessions"][0]["source"] == "request"
    assert all(days[d]["on_leave"] for d in ("2026-11-04", "2026-11-05", "2026-11-06"))

    team = client.get(
        "/api/manager/attendance?start=2026-11-01&end=2026-11-07", headers=auth(manager)
    ).json()
    alice = next(t for t in team if t["employee_id"] == "EMP0002")
    assert alice["total_minutes"] == 510


def test_manager_cannot_approve_own_request_or_other_team(client, admin_token, office_id):
    manager = _make_manager(client, admin_token, office_id)
    own = client.post(
        "/api/requests",
        headers=auth(manager),
        json={"reason": "Doctor visit", "start_date": "2026-11-03", "end_date": "2026-11-03"},
    ).json()
    r = client.post(
        f"/api/manager/requests/{own['id']}/decision",
        headers=auth(manager),
        json={"action": "approve"},
    )
    assert r.status_code == 403

    other = client.post(
        "/api/offices",
        headers=auth(admin_token),
        json={"name": "Other", "latitude": 1, "longitude": 1, "radius": 100},
    ).json()
    outsider = register(client, other["id"], "Olly", "olly@example.com")["access_token"]
    req = client.post(
        "/api/requests",
        headers=auth(outsider),
        json={"reason": "Sick day", "start_date": "2026-11-03", "end_date": "2026-11-03"},
    ).json()
    r = client.post(
        f"/api/manager/requests/{req['id']}/decision",
        headers=auth(manager),
        json={"action": "approve"},
    )
    assert r.status_code == 404
    team_ids = {
        u["employee_id"] for u in client.get("/api/manager/team", headers=auth(manager)).json()
    }
    assert "EMP0002" not in team_ids


def test_manager_can_reset_face(client, admin_token, office_id):
    manager = _make_manager(client, admin_token, office_id)
    emp = _ready_employee(client, office_id)
    r = client.delete("/api/manager/team/EMP0002/face", headers=auth(manager))
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=auth(emp)).json()["face_enrolled"] is False


def test_admin_cannot_demote_self(client, admin_token):
    r = client.patch("/api/admin/users/ADMIN", headers=auth(admin_token), json={"role": "employee"})
    assert r.status_code == 409


def test_disabled_user_cannot_log_in(client, admin_token, office_id):
    register(client, office_id)
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"active": False})
    r = client.post("/api/auth/login", json={"employee_id": "EMP0001", "password": "password123"})
    assert r.status_code == 403


def test_moving_office_moves_team(client, admin_token, office_id):
    manager = _make_manager(client, admin_token, office_id)
    register(client, office_id, "Alice", "alice@example.com")
    other = client.post(
        "/api/offices",
        headers=auth(admin_token),
        json={"name": "Other", "latitude": 1, "longitude": 1, "radius": 100},
    ).json()
    r = client.patch(
        "/api/admin/users/EMP0002", headers=auth(admin_token), json={"office_id": other["id"]}
    )
    assert r.json()["team_id"] == other["id"]
    team_ids = {
        u["employee_id"] for u in client.get("/api/manager/team", headers=auth(manager)).json()
    }
    assert "EMP0002" not in team_ids
