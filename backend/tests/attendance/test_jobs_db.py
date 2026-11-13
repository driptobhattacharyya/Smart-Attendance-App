"""Jobs, the append-only trigger, retention, and the pure helpers (matching windows, integrity)."""

import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import asyncpg
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.attendance.proximity import Candidate, match_readings
from app.attendance.rules import PolicyRules
from app.attendance.schemas import BeaconReading, LocationIn
from app.attendance.trust import (
    GooglePlayIntegrityVerifier,
    evaluate_integrity_payload,
    location_problem,
)
from app.core.config import Settings
from app.places.identity import WINDOW_SECONDS, rotating_ids
from tests.conftest import make_settings

from .helpers import API, check_in, heartbeat

JOB = {"X-Internal-Job-Secret": "job-secret-for-tests"}


@pytest.fixture
def settings(cluster):
    return make_settings(cluster, internal_job_secret="job-secret-for-tests")


def err(r):
    return r.json()["error"]["code"]


# ------------------------------------------------------------------------------ auth


async def test_job_secret_required(world, client, app):
    for headers in (
        {},
        {"X-Internal-Job-Secret": "wrong"},
        {"Authorization": "Bearer job-secret-for-tests"},
    ):
        r = await client.post(f"{API}/internal/jobs/close-stale-sessions", headers=headers)
        assert r.status_code == 401, headers
    r = await client.post(f"{API}/internal/jobs/close-stale-sessions", headers=JOB)
    assert r.status_code == 200 and r.json() == {"closed": 0, "failed": 0}
    app.state.settings.internal_job_secret = ""
    r = await client.post(f"{API}/internal/jobs/retention", headers=JOB)
    assert r.status_code == 503 and err(r) == "jobs_disabled"


# -------------------------------------------------------------------------------- jobs


async def test_close_stale_sessions_job(world, client, clock, owner_conn):
    await check_in(client, world, "emp1")
    await check_in(client, world, "emp2")
    clock.advance(minutes=5)
    await heartbeat(client, world, "emp2")  # emp2 stays alive
    clock.advance(minutes=14)
    r = await client.post(f"{API}/internal/jobs/close-stale-sessions", headers=JOB)
    assert r.json()["closed"] == 1
    closed = await owner_conn.fetch(
        "SELECT user_id, source, payload FROM attendance_events WHERE type = 'auto_check_out'"
    )
    assert [str(c["user_id"]) for c in closed] == [world.users["emp1"]["id"]]
    assert closed[0]["source"] == "system" and json.loads(closed[0]["payload"])["reason"] == "stale"
    day = await owner_conn.fetchrow(
        "SELECT flags, status FROM attendance_days WHERE user_id = $1::uuid",
        world.users["emp1"]["id"],
    )
    assert "missing_check_out" in json.loads(day["flags"]) and "auto_checked_out" in json.loads(
        day["flags"]
    )
    # idempotent
    assert (await client.post(f"{API}/internal/jobs/close-stale-sessions", headers=JOB)).json()[
        "closed"
    ] == 0


async def test_retention_job_strips_coordinates_and_deletes_samples_but_keeps_days(
    world, client, clock, owner_conn
):
    await check_in(client, world, "emp1")
    await heartbeat(client, world, "emp1")
    assert await owner_conn.fetchval("SELECT count(*) FROM presence_samples") == 1
    before = json.loads(
        await owner_conn.fetchval("SELECT payload FROM attendance_events WHERE type='check_in'")
    )
    assert "lat" in before["location"]
    r = await client.post(f"{API}/internal/jobs/retention", headers=JOB)
    assert r.json()["samples_deleted"] == 0 and r.json()["events_stripped"] == 0  # too young
    clock.advance(days=120)
    r = await client.post(f"{API}/internal/jobs/retention", headers=JOB)
    assert r.json()["samples_deleted"] == 1 and r.json()["events_stripped"] == 1
    assert await owner_conn.fetchval("SELECT count(*) FROM presence_samples") == 0
    after = json.loads(
        await owner_conn.fetchval("SELECT payload FROM attendance_events WHERE type='check_in'")
    )
    assert "lat" not in after["location"] and "lon" not in after["location"]
    assert after["location"]["distance_m"] is not None and after["coords_stripped"] is True
    assert await owner_conn.fetchval("SELECT count(*) FROM attendance_days") == 1  # totals kept
    assert (
        await owner_conn.fetchval("SELECT count(*) FROM attendance_events") == 2
    )  # nothing deleted
    assert (await client.post(f"{API}/internal/jobs/retention", headers=JOB)).json()[
        "events_stripped"
    ] == 0


async def test_recompute_job_rebuilds_days_from_events(world, client, owner_conn):
    await check_in(client, world, "emp1")
    await client.post(f"{API}/attendance/check-out", json={}, headers=world.headers["emp1"])
    await owner_conn.execute("DELETE FROM attendance_days")
    today = date.today()
    r = await client.post(
        f"{API}/internal/jobs/recompute",
        json={"start": str(today - timedelta(days=3)), "end": str(today)},
        headers=JOB,
    )
    assert r.status_code == 200 and r.json()["orgs"] >= 1 and r.json()["days"] == 4
    row = await owner_conn.fetchrow(
        "SELECT status FROM attendance_days WHERE user_id = $1::uuid AND day = $2",
        world.users["emp1"]["id"],
        today,
    )
    assert row is not None
    absent = await owner_conn.fetchval(
        "SELECT count(*) FROM attendance_days WHERE status IN ('absent','weekend')"
    )
    assert absent > 0  # past days materialised for everybody
    bad = await client.post(
        f"{API}/internal/jobs/recompute",
        json={"start": "2026-01-01", "end": "2026-12-31"},
        headers=JOB,
    )
    assert bad.status_code == 400
    scoped = await client.post(
        f"{API}/internal/jobs/recompute",
        json={"start": str(today), "end": str(today), "org_id": str(uuid.uuid4())},
        headers=JOB,
    )
    assert scoped.json()["orgs"] == 0


# ------------------------------------------------------------------- append-only trigger


async def _conn(cluster, url, org_id=None):
    conn = await asyncpg.connect(url.replace("+asyncpg", ""))
    tx = conn.transaction()
    await tx.start()
    if org_id:
        await conn.execute(f"SET LOCAL app.org_id = '{org_id}'")
    return conn, tx


async def test_events_are_append_only(world, client, cluster, owner_conn):
    await check_in(client, world, "emp1")
    org = world.id
    for url in (cluster.app_url,):
        for sql in (
            "UPDATE attendance_events SET type = 'check_out'",
            "DELETE FROM attendance_events",
        ):
            conn, tx = await _conn(cluster, url, org)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
            await tx.rollback()
            await conn.close()
    # even the table owner / superuser is stopped by the trigger
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await owner_conn.execute("UPDATE attendance_events SET occurred_at = now()")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await owner_conn.execute("DELETE FROM attendance_events")
    # the system role: only a payload change, and only while the retention flag is on
    conn, tx = await _conn(cluster, cluster.system_url)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute("UPDATE attendance_events SET payload = '{}'")
    await tx.rollback()
    for sql in (
        "UPDATE attendance_events SET occurred_at = now()",
        "UPDATE attendance_events SET payload = '{}', type = 'check_out'",
        "DELETE FROM attendance_events",
    ):
        conn, tx = await _conn(cluster, cluster.system_url)
        await conn.execute("SET LOCAL app.retention_job = 'on'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(sql)
        await tx.rollback()
        await conn.close()
    conn, tx = await _conn(cluster, cluster.system_url)
    await conn.execute("SET LOCAL app.retention_job = 'on'")
    assert await conn.execute("UPDATE attendance_events SET payload = '{}'") == "UPDATE 2"
    await tx.rollback()
    await conn.close()
    # the flag does nothing for the app role
    conn, tx = await _conn(cluster, cluster.app_url, org)
    await conn.execute("SET LOCAL app.retention_job = 'on'")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute("UPDATE attendance_events SET payload = '{}'")
    await tx.rollback()
    await conn.close()


# ------------------------------------------------------------- rotating ids at boundaries

SECRET = bytes(range(32))


def cand():
    return Candidate(
        id=uuid.uuid4(),
        kind="desk_app",
        ibeacon_uuid=None,
        major=None,
        minor=None,
        secret=SECRET,
        paired=True,
        last_seen_at=None,
    )


@pytest.mark.parametrize(
    ("t", "window", "matches"),
    [
        (0.0, 0, True),
        (0.0, 1, True),  # w+1 accepted
        (0.0, 2, False),
        (299.999, 1, True),
        (299.999, 2, False),
        (300.0, 0, True),  # w-1 accepted right after the boundary
        (300.0, 1, True),
        (300.0, 2, True),
        (300.0, 3, False),
        (600.0, 0, False),  # two windows old
        (600.0, 1, True),
        (5_000_000 * 300 + 150.0, 5_000_000 - 1, True),
        (5_000_000 * 300 + 150.0, 5_000_000 + 2, False),
    ],
)
def test_rotating_id_window_boundaries(t, window, matches):
    c, u = cand(), uuid.uuid4()
    major, minor = rotating_ids(SECRET, window)
    at = datetime.fromtimestamp(t, UTC)
    found = match_readings([BeaconReading(uuid=u, major=major, minor=minor, rssi=-50)], [c], u, at)
    assert bool(found) is matches
    assert WINDOW_SECONDS == 300


def test_two_desks_with_colliding_ids_both_match():
    a, b, u = cand(), cand(), uuid.uuid4()
    major, minor = rotating_ids(SECRET, 10)
    out = match_readings(
        [BeaconReading(uuid=u, major=major, minor=minor, rssi=-50)],
        [a, b],
        u,
        datetime.fromtimestamp(3000, UTC),
    )
    assert {c.id for c in out[0][1]} == {a.id, b.id}


def test_reading_with_foreign_uuid_never_matches_a_desk_app():
    c = cand()
    major, minor = rotating_ids(SECRET, 10)
    out = match_readings(
        [BeaconReading(uuid=uuid.uuid4(), major=major, minor=minor, rssi=-50)],
        [c],
        uuid.uuid4(),
        datetime.fromtimestamp(3000, UTC),
    )
    assert out == []


# ------------------------------------------------------------------------ trust units


def test_location_problem_order_and_limits():
    rules, now = PolicyRules(), datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    ok = LocationIn(lat=1, lon=2, accuracy_m=99, captured_at=now - timedelta(seconds=119))
    assert location_problem(ok, rules, now) is None
    both = LocationIn(lat=1, lon=2, mock=True, accuracy_m=500)
    assert location_problem(both, rules, now).code == "mock_location"
    stale = LocationIn(lat=1, lon=2, captured_at=now - timedelta(seconds=121))
    assert location_problem(stale, rules, now).code == "location_stale"
    future = LocationIn(lat=1, lon=2, captured_at=now + timedelta(minutes=5))
    assert location_problem(future, rules, now).code == "location_stale"
    assert (
        location_problem(LocationIn(lat=1, lon=2, accuracy_m=100.1), rules, now).code
        == "location_inaccurate"
    )


def payload(**over):
    base = {
        "requestDetails": {
            "requestPackageName": "com.acme.app",
            "nonce": "n1",
            "timestampMillis": str(int(time.time() * 1000)),
        },
        "appIntegrity": {
            "appRecognitionVerdict": "PLAY_RECOGNIZED",
            "packageName": "com.acme.app",
            "certificateSha256Digest": ["abc"],
        },
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
    }
    for k, v in over.items():
        base[k] = {**base[k], **v}
    return base


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({}, "ok"),
        (
            {"deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_BASIC_INTEGRITY"]}},
            "device_integrity_failed",
        ),
        ({"deviceIntegrity": {"deviceRecognitionVerdict": []}}, "device_integrity_failed"),
        ({"appIntegrity": {"appRecognitionVerdict": "UNRECOGNIZED_VERSION"}}, "app_not_recognized"),
        ({"appIntegrity": {"packageName": "evil.app"}}, "wrong_package"),
        ({"appIntegrity": {"certificateSha256Digest": ["zzz"]}}, "wrong_certificate"),
        ({"requestDetails": {"requestPackageName": "evil.app"}}, "wrong_request_package"),
        ({"requestDetails": {"nonce": "other"}}, "nonce_mismatch"),
        ({"requestDetails": {"timestampMillis": "1000"}}, "token_not_fresh"),
    ],
)
def test_integrity_payload_decisions(over, reason):
    v = evaluate_integrity_payload(
        payload(**over),
        package_name="com.acme.app",
        cert_digests={"abc"},
        now=datetime.now(UTC),
        nonce="n1",
    )
    assert (v.ok, v.reason) == (reason == "ok", reason)


def _service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps(
        {
            "client_email": "sa@proj.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": "https://oauth2.example/token",
        }
    )


async def test_google_verifier_calls_decode_with_service_account_auth(cluster):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.example":
            assert b"jwt-bearer" in request.content
            return httpx.Response(200, json={"access_token": "ya29.tok", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer ya29.tok"
        assert (
            str(request.url)
            == "https://playintegrity.googleapis.com/v1/com.acme.app:decodeIntegrityToken"
        )
        assert json.loads(request.content) == {"integrity_token": "tok-1"}
        return httpx.Response(200, json={"tokenPayloadExternal": payload()})

    settings: Settings = make_settings(
        cluster,
        play_integrity_enabled=True,
        play_integrity_package_name="com.acme.app",
        play_integrity_cert_sha256="abc",
        play_integrity_service_account_json=_service_account(),
    )
    verifier = GooglePlayIntegrityVerifier(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert (await verifier.verify("tok-1", "n1")).ok
    assert (await verifier.verify("tok-1", "n1")).ok
    assert len([c for c in calls if c.url.host == "oauth2.example"]) == 1  # access token cached
    bad = await verifier.verify("tok-1", "different-nonce")
    assert not bad.ok and bad.reason == "nonce_mismatch"


async def test_google_verifier_fails_closed_when_google_is_down(cluster):
    def handler(request):
        return httpx.Response(500)

    settings = make_settings(
        cluster,
        play_integrity_enabled=True,
        play_integrity_package_name="com.acme.app",
        play_integrity_service_account_json=_service_account(),
    )
    v = await GooglePlayIntegrityVerifier(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).verify("t")
    assert not v.ok and v.unavailable


def test_play_integrity_needs_configuration(cluster):
    with pytest.raises(RuntimeError):
        GooglePlayIntegrityVerifier(make_settings(cluster, play_integrity_enabled=True))
