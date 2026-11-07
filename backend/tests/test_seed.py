import argparse
import asyncio

from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.main import create_app
from app.security import create_access_token
from scripts import seed

ARGS = argparse.Namespace(office="HQ", lat=22.5726, lon=88.3639, radius=150, tz="Asia/Kolkata")


async def _run(db):
    office = await seed._ensure_office(db, "HQ", 22.5726, 88.3639, 150, "Asia/Kolkata", "09:30")
    await seed._create_user(
        db, "Demo Admin", "admin@attendify.demo", "admin", str(office["_id"]), "x"
    )
    await seed._seed_demo(db, office, ARGS)


def test_demo_seed_is_complete_and_consistent():
    db = AsyncMongoMockClient(tz_aware=True)["seed"]
    asyncio.run(_run(db))
    users = asyncio.run(db.users.find().to_list(None))
    assert len(users) == 13 and all(u["consent"]["version"] == 1 for u in users)
    assert asyncio.run(db.offices.count_documents({})) == 2
    assert asyncio.run(db.beacons.count_documents({"assigned_employee_id": None})) >= 3
    assert asyncio.run(db.attendance_requests.count_documents({"status": "Pending"})) == 1
    assert asyncio.run(db.attendance_requests.count_documents({"kind": "leave"})) == 1
    sessions = asyncio.run(db.attendance_sessions.find().to_list(None))
    assert len(sessions) > 50
    assert any(s["away_seconds"] > 0 for s in sessions)

    # Idempotent
    office = asyncio.run(db.offices.find_one({"name": "HQ"}))
    asyncio.run(seed._seed_demo(db, office, ARGS))
    assert asyncio.run(db.users.count_documents({})) == 13
    assert asyncio.run(db.attendance_sessions.count_documents({})) == len(sessions)

    # The console endpoints work on it
    settings = Settings(_env_file=None, jwt_secret="test-secret-test-secret-test-secret")
    token = create_access_token("EMP0001", "admin", settings)
    with TestClient(create_app(settings, db=db)) as client:
        h = {"Authorization": f"Bearer {token}"}
        dash = client.get("/api/manager/dashboard", headers=h).json()
        assert dash["headcount"]["total"] == 13 and dash["pending_requests"] == 1
        assert client.get("/api/manager/presence", headers=h).status_code == 200
        r = client.get(
            "/api/manager/attendance/export.csv",
            params={"start": "2020-01-01", "end": "2030-01-01"},
            headers=h,
        )
        assert r.status_code == 422  # too long a range, but routed correctly
