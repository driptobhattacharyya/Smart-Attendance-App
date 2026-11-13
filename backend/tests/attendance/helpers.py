"""Shared helpers for the attendance API tests: everything goes through the real HTTP API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.face.fakes import frame

API = "/api/v1"
HQ = {"lat": 12.9716, "lon": 77.5946}
OFFICE = {
    "name": "HQ",
    "latitude": HQ["lat"],
    "longitude": HQ["lon"],
    "radius_m": 150,
    "timezone": "UTC",
    "work_start": "00:00",
    "work_end": "23:59",
}
TAG_UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"


def loc(lat=HQ["lat"], lon=HQ["lon"], **kw):
    return {"lat": lat, "lon": lon, "accuracy_m": 10, "mock": False, **kw}


def files(*frames: bytes):
    return [("frames", (f"f{i}.jpg", data, "image/jpeg")) for i, data in enumerate(frames)]


async def make_office(client, org, **overrides) -> dict:
    r = await client.post(
        f"{API}/places/offices", json={**OFFICE, **overrides}, headers=org.owner_headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def assign_office(client, org, key: str, office: dict):
    r = await client.patch(
        f"{API}/people/{org.users[key]['id']}",
        json={"office_id": office["id"]},
        headers=org.owner_headers,
    )
    assert r.status_code == 200, r.text


async def consent(client, org, key: str):
    h = org.headers[key]
    me = (await client.get(f"{API}/people/me", headers=h)).json()
    r = await client.post(f"{API}/auth/consent", json={"version": me["consent_version"]}, headers=h)
    assert r.status_code == 200, r.text


async def enroll(client, org, key: str, who: str | None = None):
    who = who or key
    r = await client.post(
        f"{API}/face/enroll",
        files=files(frame(who, seed=1), frame(who, seed=2), frame(who, seed=3)),
        headers=org.headers[key],
    )
    assert r.status_code == 201, r.text
    return r.json()


async def verify_face(client, org, key: str, who: str | None = None, *, spoof=0.95):
    who = who or key
    h = org.headers[key]
    ch = (await client.get(f"{API}/face/challenge", headers=h)).json()
    d = 1 if ch["action"] == "turn_left" else -1
    r = await client.post(
        f"{API}/face/verify",
        data={"challenge_token": ch["token"]},
        files=files(
            frame(who, 0, seed=10, spoof=spoof),
            frame(who, 8 * d, seed=11, spoof=spoof),
            frame(who, 28 * d, seed=12, spoof=spoof),
        ),
        headers=h,
    )
    return r


async def ready(client, org, key: str, office: dict | None = None):
    """Consent + office + face enrolment for an existing org user."""
    await consent(client, org, key)
    if office is not None:
        await assign_office(client, org, key, office)
    await enroll(client, org, key)


async def check_in(client, org, key, *, verify=True, json=None, headers=None):
    if verify:
        r = await verify_face(client, org, key)
        assert r.status_code == 200, r.text
    body = {"mode": "onsite", "location": loc(), **(json or {})}
    return await client.post(
        f"{API}/attendance/check-in", json=body, headers={**org.headers[key], **(headers or {})}
    )


async def heartbeat(client, org, key, *, json=None, headers=None):
    body = {"location": loc(), **(json or {})}
    return await client.post(
        f"{API}/attendance/heartbeat", json=body, headers={**org.headers[key], **(headers or {})}
    )


async def check_out(client, org, key, headers=None):
    return await client.post(
        f"{API}/attendance/check-out", json={}, headers={**org.headers[key], **(headers or {})}
    )


async def event_types(owner_conn, user_id) -> list[str]:
    rows = await owner_conn.fetch(
        "SELECT type FROM attendance_events WHERE user_id = $1::uuid "
        "ORDER BY occurred_at, recorded_at",
        user_id,
    )
    return [r["type"] for r in rows]


class Clock:
    """Shift 'now' for the attendance, face and jobs modules (they import ``utcnow`` by name)."""

    def __init__(self, monkeypatch):
        self.offset = timedelta(0)
        from app.attendance import face_api, reports, service
        from app.jobs import service as jobs

        real = datetime.now

        def now():
            return real(UTC) + self.offset

        for module in (service, face_api, jobs, reports):
            monkeypatch.setattr(module, "utcnow", now)

    def advance(self, **kw):
        self.offset += timedelta(**kw)
