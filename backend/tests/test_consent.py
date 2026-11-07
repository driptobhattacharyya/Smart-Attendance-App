"""Consent, data export and retention (contract v3, last section)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from .conftest import INSIDE, auth, enroll, login, register, verify

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=UTC)


def _session(db, employee_id, date, **extra):
    doc = {
        "employee_id": employee_id,
        "team_id": "t",
        "office_id": "o",
        "date": date,
        "started_at": NOW - timedelta(days=100),
        "ended_at": NOW - timedelta(days=100) + timedelta(hours=8),
        "source": "app",
        "last_state": "present",
        "present_seconds": 7 * 3600,
        "away_seconds": 600,
        "last_heartbeat_at": NOW,
        "presence_log": [{"at": NOW, "state": "present", "distance_m": 1.0, "best_rssi": -60}],
        "check_in": {"latitude": 1.0, "longitude": 2.0, "distance_m": 3.0, "best_rssi": -60},
        "check_out": {"latitude": 1.0, "longitude": 2.0},
        **extra,
    }
    asyncio.run(db.attendance_sessions.insert_one(doc))
    return doc


@pytest.fixture
def unconsented(client, office_id):
    body = register(client, office_id, consent=False)
    return body["access_token"]


# ---------- accepting ----------


def test_consent_required_flag_everywhere(client, office_id):
    body = register(client, office_id, consent=False)
    assert body["consent_required"] is True and body["user"]["consent_required"] is True
    token = body["access_token"]
    assert client.get("/api/auth/me", headers=auth(token)).json()["consent_required"] is True
    login_body = client.post(
        "/api/auth/login", json={"employee_id": "EMP0001", "password": "password123"}
    ).json()
    assert login_body["consent_required"] is True

    r = client.post("/api/auth/consent", headers=auth(token), json={"version": 1})
    assert r.status_code == 200 and r.json()["consent_required"] is False
    assert client.get("/api/auth/me", headers=auth(token)).json()["consent_required"] is False
    login_body = client.post(
        "/api/auth/login", json={"employee_id": "EMP0001", "password": "password123"}
    ).json()
    assert login_body["consent_required"] is False


def test_consent_stores_version_and_time(client, db, unconsented):
    client.post("/api/auth/consent", headers=auth(unconsented), json={"version": 1})
    user = asyncio.run(db.users.find_one({"employee_id": "EMP0001"}))
    assert user["consent"]["version"] == 1 and user["consent"]["accepted_at"]


def test_consent_wrong_version_is_409(client, unconsented):
    r = client.post("/api/auth/consent", headers=auth(unconsented), json={"version": 7})
    assert r.status_code == 409
    assert client.get("/api/auth/me", headers=auth(unconsented)).json()["consent_required"]
    assert client.post("/api/auth/consent", json={"version": 1}).status_code == 401


def test_version_bump_requires_consent_again(client, settings, office_id):
    token = register(client, office_id)["access_token"]
    assert client.get("/api/auth/me", headers=auth(token)).json()["consent_required"] is False
    settings.consent_version = 2
    assert client.get("/api/auth/me", headers=auth(token)).json()["consent_required"] is True
    assert (
        client.post("/api/auth/consent", headers=auth(token), json={"version": 1}).status_code
        == 409
    )
    assert (
        client.post("/api/auth/consent", headers=auth(token), json={"version": 2}).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=auth(token)).json()["consent_required"] is False


def test_admin_user_list_shows_consent(client, admin_token, office_id):
    register(client, office_id, consent=False)
    users = {
        u["employee_id"]: u
        for u in client.get("/api/admin/users", headers=auth(admin_token)).json()
    }
    assert users["ADMIN"]["consent_required"] is False
    assert users["EMP0001"]["consent_required"] is True


# ---------- enforcement ----------


def test_enforcement_on_all_four_endpoints(client, unconsented):
    h = auth(unconsented)
    image = {"image": ("f.jpg", b"face:alice", "image/jpeg")}
    body = {**INSIDE, "beacons": []}
    for r in (
        client.post("/api/attendance/check-in", headers=h, json=body),
        client.post("/api/attendance/heartbeat", headers=h, json=body),
        client.post("/api/face/enroll", headers=h, files=image),
        client.post("/api/face/verify", headers=h, files=image),
    ):
        assert r.status_code == 403
        assert r.json() == {"detail": "Consent required"}
    # nothing was stored
    assert client.get("/api/auth/me", headers=h).json()["face_enrolled"] is False
    # reading your own status and checking out are still allowed
    assert client.get("/api/attendance/status", headers=h).status_code == 200
    assert client.post("/api/attendance/check-out", headers=h).status_code == 409  # not checked in


def test_enforcement_lifts_after_consent(client, settings, office_id):
    settings.require_bluetooth = False
    token = register(client, office_id, consent=False)["access_token"]
    client.post("/api/auth/consent", headers=auth(token), json={"version": 1})
    enroll(client, token)
    assert verify(client, token).status_code == 200
    r = client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE)
    assert r.status_code == 201


# ---------- withdrawing ----------


def test_withdraw_checks_out_and_blocks_tracking(client, db, clock, office_id):
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    verify(client, token)
    assert (
        client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE).status_code == 201
    )
    clock.advance(minutes=10)

    r = client.post("/api/auth/consent/withdraw", headers=auth(token))
    assert r.status_code == 200 and r.json()["consent_required"] is True
    doc = asyncio.run(db.attendance_sessions.find_one({"employee_id": "EMP0001"}))
    assert doc["ended_at"] is not None
    assert doc["present_seconds"] == 600  # the last interval was credited
    user = asyncio.run(db.users.find_one({"employee_id": "EMP0001"}))
    assert "consent" not in user and "face_verified_at" not in user
    assert client.get("/api/attendance/status", headers=auth(token)).json()["checked_in"] is False
    body = {**INSIDE, "beacons": []}
    assert (
        client.post("/api/attendance/heartbeat", headers=auth(token), json=body).status_code == 403
    )
    assert (
        client.post("/api/attendance/check-in", headers=auth(token), json=body).status_code == 403
    )

    # withdrawing twice is harmless, and agreeing again restores access
    assert client.post("/api/auth/consent/withdraw", headers=auth(token)).status_code == 200
    client.post("/api/auth/consent", headers=auth(token), json={"version": 1})
    assert verify(client, token).status_code == 200


# ---------- /auth/me/export ----------


def test_export_contents_and_exclusions(client, db, office_id):
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    _session(db, "EMP0001", "2026-01-01")
    client.post(
        "/api/requests",
        headers=auth(token),
        json={"reason": "Forgot", "start_date": "2026-11-03", "end_date": "2026-11-03",
              "entry_time": "09:30", "exit_time": "18:00"},
    )  # fmt: skip
    r = client.get("/api/auth/me/export", headers=auth(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"generated_at", "profile", "sessions", "requests"}
    profile = data["profile"]
    assert profile["employee_id"] == "EMP0001" and profile["email"] == "alice@example.com"
    assert profile["face_enrolled"] is True
    assert profile["consent"]["version"] == 1
    assert "password_hash" not in profile and "face_embedding" not in profile
    text = r.text
    assert "password_hash" not in text and "face_embedding" not in text
    assert "$argon2" not in text
    assert len(data["sessions"]) == 1
    session = data["sessions"][0]
    assert session["presence_log"][0]["best_rssi"] == -60 and session["id"]
    assert len(data["requests"]) == 1 and data["requests"][0]["reason"] == "Forgot"


def test_export_only_contains_own_data_and_works_without_consent(client, db, office_id):
    token = register(client, office_id)["access_token"]
    register(client, office_id, "Bob", "bob@example.com")
    _session(db, "EMP0002", "2026-01-01")
    client.post("/api/auth/consent/withdraw", headers=auth(token))
    r = client.get("/api/auth/me/export", headers=auth(token))
    assert r.status_code == 200 and r.json()["sessions"] == []
    assert client.get("/api/auth/me/export").status_code == 401


# ---------- /consent/text ----------


def test_consent_text_is_public_and_rendered(client, settings):
    settings.presence_retention_days = 45
    r = client.get("/api/consent/text")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1 and body["title"]
    md = body["body_markdown"]
    for heading in (
        "What we collect",
        "Why",
        "Who can see it",
        "How long we keep it",
        "Your choices",
    ):
        assert f"## {heading}" in md
    assert "45 days" in md
    assert "{" not in md and "}" not in md
    assert "employer" in md and "withdraw" in md and "Microsoft" not in md
    assert "on our server" in md


def test_consent_text_describes_azure_accurately(client, settings):
    settings.face_provider = "azure"
    md = client.get("/api/consent/text").json()["body_markdown"]
    assert "sent to Microsoft Azure" in md
    assert "on our server" not in md  # the opencv wording must not appear


def test_consent_text_follows_version(client, settings):
    settings.consent_version = 3
    assert client.get("/api/consent/text").json()["version"] == 3


# ---------- retention ----------


def _details(db, employee_id):
    return asyncio.run(db.attendance_sessions.find({"employee_id": employee_id}).to_list(None))


def test_admin_prune_removes_old_details_but_keeps_totals(client, db, admin_token, clock):
    clock.now = NOW
    old = _session(db, "E1", "2026-08-01")  # 100 days before
    _session(db, "E1", "2026-08-11")  # exactly 90 days old: kept
    _session(db, "E1", "2026-11-01")
    r = client.post("/api/admin/maintenance/prune", headers=auth(admin_token))
    assert r.status_code == 200 and r.json() == {"sessions_pruned": 1}
    docs = {d["date"]: d for d in _details(db, "E1")}
    pruned = docs["2026-08-01"]
    for field in ("presence_log", "check_in", "check_out"):
        assert field not in pruned
    assert pruned["present_seconds"] == old["present_seconds"] == 7 * 3600
    assert pruned["away_seconds"] == 600 and pruned["source"] == "app"
    assert pruned["started_at"] and pruned["ended_at"]
    assert "presence_log" in docs["2026-08-11"] and "check_in" in docs["2026-11-01"]
    # idempotent
    assert client.post("/api/admin/maintenance/prune", headers=auth(admin_token)).json() == {
        "sessions_pruned": 0
    }


def test_prune_respects_retention_setting_and_totals_in_reports(
    client, db, admin_token, settings, clock, office_id
):
    clock.now = NOW
    settings.presence_retention_days = 10
    _session(db, "ADMIN", "2026-10-20")
    _session(db, "ADMIN", "2026-11-05")
    assert client.post("/api/admin/maintenance/prune", headers=auth(admin_token)).json() == {
        "sessions_pruned": 1
    }
    report = client.get(
        "/api/attendance/me", params={"start": "2026-10-01", "end": "2026-11-09"},
        headers=auth(admin_token),
    ).json()  # fmt: skip
    assert report["total_minutes"] == 2 * 420  # both days still count


def test_prune_endpoint_is_admin_only(client, admin_token, office_id):
    token = register(client, office_id)["access_token"]
    assert client.post("/api/admin/maintenance/prune", headers=auth(token)).status_code == 403
    assert client.post("/api/admin/maintenance/prune").status_code == 401
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"})
    manager = login(client, "EMP0001", "password123")
    assert client.post("/api/admin/maintenance/prune", headers=auth(manager)).status_code == 403


def test_prune_runs_opportunistically_on_check_in_once_an_hour(
    client, db, settings, clock, office_id, monkeypatch
):
    monkeypatch.setattr("app.routers.face.utcnow", clock)  # face scans must age with the clock
    settings.require_bluetooth = False
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    _session(db, "ZZ", "2000-01-01")

    def check_in_and_out():
        verify(client, token)
        assert (
            client.post("/api/attendance/check-in", headers=auth(token), json=INSIDE).status_code
            == 201
        )
        assert client.post("/api/attendance/check-out", headers=auth(token)).status_code == 200

    check_in_and_out()
    assert "presence_log" not in _details(db, "ZZ")[0]

    _session(db, "YY", "2000-01-01")
    clock.advance(minutes=30)
    check_in_and_out()
    assert "presence_log" in _details(db, "YY")[0]  # throttled: ran less than an hour ago

    clock.advance(minutes=31)
    check_in_and_out()
    assert "presence_log" not in _details(db, "YY")[0]
