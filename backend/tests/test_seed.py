from app.core.security import PasswordHasher
from scripts.seed import DEMO_PASSWORD, seed

API = "/api/v1"


async def test_seed_builds_the_demo_org_and_is_rerunnable(app, client, settings, owner_conn):
    hasher = PasswordHasher(settings)
    summary = await seed(app.state.db, settings, hasher)
    assert (
        summary
        and summary["users"] == 16
        and summary["offices"] == 2
        and summary["departments"] == 3
    )

    assert await owner_conn.fetchval("SELECT count(*) FROM users WHERE status = 'active'") == 16
    assert await owner_conn.fetchval("SELECT count(DISTINCT office_id) FROM users") == 2
    assert await owner_conn.fetchval("SELECT count(*) FROM desk_devices") == 4
    assert await owner_conn.fetchval("SELECT count(*) FROM users WHERE role_key = 'manager'") == 2

    login = await client.post(
        f"{API}/auth/login", json={"email": summary["owner_email"], "password": DEMO_PASSWORD}
    )
    assert login.status_code == 200
    # Reporting tree works through the real API: the first manager sees reports + the 2nd manager.
    mgr = await client.post(
        f"{API}/auth/login",
        json={"email": "meera.manager@demo.example.com", "password": DEMO_PASSWORD},
    )
    h = {"Authorization": f"Bearer {mgr.json()['access_token']}"}
    team = (await client.get(f"{API}/people?limit=200", headers=h)).json()["items"]
    assert len(team) == 13  # 6 direct employees + Manoj + his 6 employees
    # Pairing code printed by the seed works for the desk app.
    paired = await client.post(f"{API}/desk/pair", json={"code": summary["desk_pairing_code"]})
    assert paired.status_code == 200

    assert await seed(app.state.db, settings, hasher) is None
