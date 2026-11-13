import base64

from app.places.identity import match_reading
from app.places.service import normalize_code

from .conftest import bearer

API = "/api/v1"
OFFICE = {
    "name": "HQ",
    "latitude": 12.97,
    "longitude": 77.59,
    "radius_m": 120,
    "timezone": "Asia/Kolkata",
}
TAG_UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"


async def enable_desk_app(client, org):
    r = await client.patch(
        f"{API}/org", json={"modules": {"desk_app": True}}, headers=org.owner_headers
    )
    assert r.status_code == 200


async def desk_app(client, org, **extra):
    await enable_desk_app(client, org)
    r = await client.post(
        f"{API}/places/desk-devices",
        json={"kind": "desk_app", "name": "Desk 1", **extra},
        headers=org.owner_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def pair(client, org, device, **body):
    code = (
        await client.post(
            f"{API}/places/desk-devices/{device['id']}/pairing-code", headers=org.owner_headers
        )
    ).json()["code"]
    r = await client.post(
        f"{API}/desk/pair", json={"code": code, "hostname": "PC-1", "os": "Windows 11", **body}
    )
    return code, r


# ------------------------------------------------------------------------------ offices


async def test_office_crud_and_validation(org, client):
    h = org.owner_headers
    r = await client.post(
        f"{API}/places/offices",
        json={**OFFICE, "work_start": "09:00", "work_end": "18:00"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["work_start"] == "09:00:00" and o["active"] is True
    bad = [
        {**OFFICE, "latitude": 91},
        {**OFFICE, "timezone": "Mars/Base"},
        {**OFFICE, "radius_m": 0},
    ]
    for payload in bad:
        assert (
            await client.post(f"{API}/places/offices", json=payload, headers=h)
        ).status_code == 422
    p = await client.patch(
        f"{API}/places/offices/{o['id']}", json={"radius_m": 200, "active": False}, headers=h
    )
    assert p.json()["radius_m"] == 200 and p.json()["active"] is False
    assert (
        await client.patch(f"{API}/places/offices/{o['id']}", json={"name": None}, headers=h)
    ).status_code == 400
    assert len((await client.get(f"{API}/places/offices", headers=h)).json()) == 1
    assert (await client.delete(f"{API}/places/offices/{o['id']}", headers=h)).status_code == 204
    assert (await client.get(f"{API}/places/offices/{o['id']}", headers=h)).status_code == 404


async def test_employees_can_read_offices_but_not_write(team_org, client):
    o = (
        await client.post(f"{API}/places/offices", json=OFFICE, headers=team_org.owner_headers)
    ).json()
    h = team_org.headers["emp1"]
    assert (await client.get(f"{API}/places/offices", headers=h)).status_code == 200
    assert (await client.get(f"{API}/places/offices/{o['id']}", headers=h)).status_code == 200
    assert (await client.post(f"{API}/places/offices", json=OFFICE, headers=h)).status_code == 403
    assert (
        await client.patch(f"{API}/places/offices/{o['id']}", json={"name": "x"}, headers=h)
    ).status_code == 403
    assert (await client.delete(f"{API}/places/offices/{o['id']}", headers=h)).status_code == 403


async def test_office_in_use_cannot_be_deleted(org, client):
    h = org.owner_headers
    o = (await client.post(f"{API}/places/offices", json=OFFICE, headers=h)).json()
    await client.post(
        f"{API}/people",
        json={"name": "A", "email": "a@example.com", "office_id": o["id"]},
        headers=h,
    )
    r = await client.delete(f"{API}/places/offices/{o['id']}", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "office_in_use"


# ------------------------------------------------------------------------------- devices


async def test_tag_crud_and_unique_identity(org, client):
    h = org.owner_headers
    body = {"kind": "tag", "name": "Tag A", "ibeacon_uuid": TAG_UUID, "major": 1, "minor": 2}
    r = await client.post(f"{API}/places/desk-devices", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["major"] == 1 and r.json()["paired"] is False
    dup = await client.post(f"{API}/places/desk-devices", json={**body, "name": "Tag B"}, headers=h)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "beacon_identity_taken"
    assert (
        await client.post(
            f"{API}/places/desk-devices", json={"kind": "tag", "name": "x"}, headers=h
        )
    ).status_code == 422
    upd = await client.patch(
        f"{API}/places/desk-devices/{r.json()['id']}", json={"minor": 9, "active": False}, headers=h
    )
    assert upd.json()["minor"] == 9 and upd.json()["active"] is False
    assert (
        await client.delete(f"{API}/places/desk-devices/{r.json()['id']}", headers=h)
    ).status_code == 204


async def test_desk_app_requires_module_and_has_no_fixed_identity(org, client):
    h = org.owner_headers
    r = await client.post(
        f"{API}/places/desk-devices", json={"kind": "desk_app", "name": "D"}, headers=h
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    await enable_desk_app(client, org)
    r = await client.post(
        f"{API}/places/desk-devices", json={"kind": "desk_app", "name": "D", "major": 1}, headers=h
    )
    assert r.status_code == 422
    d = await desk_app(client, org)
    assert d["major"] is None and d["minor"] is None and d["paired"] is False
    assert d["ibeacon_uuid"] == org.signup["org"]["settings"]["desk_uuid"]


async def test_devices_are_assigned_to_users_and_offices_in_the_same_org_only(make_org, client):
    a, b = await make_org("A Co"), await make_org("B Co")
    other_office = (
        await client.post(f"{API}/places/offices", json=OFFICE, headers=b.owner_headers)
    ).json()
    body = {"kind": "tag", "name": "T", "ibeacon_uuid": TAG_UUID, "major": 1, "minor": 1}
    r = await client.post(
        f"{API}/places/desk-devices",
        json={**body, "office_id": other_office["id"]},
        headers=a.owner_headers,
    )
    assert r.status_code == 404
    r = await client.post(
        f"{API}/places/desk-devices",
        json={**body, "assigned_user_id": b.users["owner"]["id"]},
        headers=a.owner_headers,
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_user"


async def test_pairing_returns_secret_once_and_matches_rotating_ids(org, client, app, owner_conn):
    d = await desk_app(client, org, assigned_user_id=org.users["owner"]["id"])
    code, r = await pair(client, org, d, agent_version="1.0.0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["device_token"].startswith("adt_")
    secret = base64.b64decode(body["secret"])
    assert len(secret) == 32
    assert body["beacon"]["id"] == d["id"] and body["beacon"]["uuid"] == d["ibeacon_uuid"]
    assert body["beacon"]["window_seconds"] == 300
    assert body["employee"]["id"] == org.users["owner"]["id"]
    assert body["heartbeat_interval_seconds"] == 60 and "server_time" in body
    assert "major" not in body and "minor" not in body

    # Stored encrypted (not the raw secret), and decryptable with DATA_KEY to the same secret.
    row = await owner_conn.fetchrow("SELECT secret_enc, device_token_hash FROM desk_devices")
    assert (
        secret not in bytes(row["secret_enc"])
        and body["device_token"] not in row["device_token_hash"]
    )
    import uuid

    from app.places.desk_system import decrypt_device_secret

    assert (
        decrypt_device_secret(app.state.settings, uuid.UUID(d["id"]), bytes(row["secret_enc"]))
        == secret
    )
    assert (
        match_reading(
            secret,
            *__import__("app.places.identity", fromlist=["x"]).rotating_ids(secret, 5),
            5 * 300 + 1,
        )
        == 5
    )

    # The code is single use, and the secret never appears again anywhere.
    again = await client.post(f"{API}/desk/pair", json={"code": code})
    assert again.status_code == 404 and again.json()["error"]["code"] == "invalid_code"
    listing = await client.get(f"{API}/places/desk-devices", headers=org.owner_headers)
    one = await client.get(f"{API}/places/desk-devices/{d['id']}", headers=org.owner_headers)
    for resp in (listing, one):
        text = resp.text
        assert (
            body["secret"] not in text and body["device_token"] not in text and "secret" not in text
        )
    assert one.json()["paired"] is True and one.json()["device"]["hostname"] == "PC-1"


async def test_code_format_is_forgiving_and_wrong_or_expired_codes_fail(org, client, owner_conn):
    d = await desk_app(client, org)
    code = (
        await client.post(
            f"{API}/places/desk-devices/{d['id']}/pairing-code", headers=org.owner_headers
        )
    ).json()["code"]
    assert len(code) == 9 and code[4] == "-"
    assert (await client.post(f"{API}/desk/pair", json={"code": "AAAA-BBBB"})).status_code == 404
    await owner_conn.execute(
        "UPDATE desk_devices SET pairing_expires_at = now() - interval '1 second'"
    )
    assert (await client.post(f"{API}/desk/pair", json={"code": code})).status_code == 404
    code2 = (
        await client.post(
            f"{API}/places/desk-devices/{d['id']}/pairing-code", headers=org.owner_headers
        )
    ).json()["code"]
    sloppy = " " + code2.lower().replace("-", " ") + " "
    assert normalize_code(sloppy) == code2.replace("-", "")
    assert (await client.post(f"{API}/desk/pair", json={"code": sloppy})).status_code == 200


async def test_pair_attempts_are_rate_limited(cluster):
    import httpx

    from app.main import create_app

    from .conftest import make_settings

    app = create_app(make_settings(cluster, pair_max_failures=3))
    transport = httpx.ASGITransport(app=app, client=("192.0.2.5", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        codes = [
            (await c.post(f"{API}/desk/pair", json={"code": f"AAAA-BB{i}{i}"})).status_code
            for i in range(5)
        ]
    await app.state.db.dispose()
    assert codes == [404, 404, 404, 429, 429]


async def test_tags_cannot_be_paired_and_unpair_revokes_the_device(org, client):
    tag = (
        await client.post(
            f"{API}/places/desk-devices",
            json={"kind": "tag", "name": "T", "ibeacon_uuid": TAG_UUID, "major": 1, "minor": 1},
            headers=org.owner_headers,
        )
    ).json()
    assert (
        await client.post(
            f"{API}/places/desk-devices/{tag['id']}/pairing-code", headers=org.owner_headers
        )
    ).status_code == 400
    d = await desk_app(client, org)
    _, r = await pair(client, org, d)
    token = r.json()["device_token"]
    hb = {"Authorization": f"Device {token}"}
    assert (await client.post(f"{API}/desk/heartbeat", json={}, headers=hb)).status_code == 200
    un = await client.post(f"{API}/places/desk-devices/{d['id']}/unpair", headers=org.owner_headers)
    assert un.status_code == 200 and un.json()["paired"] is False
    assert (await client.post(f"{API}/desk/heartbeat", json={}, headers=hb)).status_code == 401


async def test_repairing_replaces_token_and_secret(org, client):
    d = await desk_app(client, org)
    _, first = await pair(client, org, d)
    _, second = await pair(client, org, d)
    assert first.json()["secret"] != second.json()["secret"]
    old = {"Authorization": f"Device {first.json()['device_token']}"}
    new = {"Authorization": f"Device {second.json()['device_token']}"}
    assert (await client.post(f"{API}/desk/heartbeat", json={}, headers=old)).status_code == 401
    assert (await client.post(f"{API}/desk/heartbeat", json={}, headers=new)).status_code == 200


async def test_heartbeat_records_activity_state_and_returns_server_time(org, client):
    d = await desk_app(client, org)
    _, r = await pair(client, org, d)
    hb = {"Authorization": f"Device {r.json()['device_token']}"}
    resp = await client.post(
        f"{API}/desk/heartbeat",
        json={"locked": True, "idle_seconds": 321, "advertising": True, "agent_version": "1.2.3"},
        headers=hb,
    )
    assert resp.status_code == 200
    out = resp.json()
    assert (
        out["heartbeat_interval_seconds"] == 60
        and out["server_time"].endswith("Z")
        or "+" in out["server_time"]
    )
    assert "major" not in out and "minor" not in out
    state = (
        await client.get(f"{API}/places/desk-devices/{d['id']}", headers=org.owner_headers)
    ).json()
    assert state["device"]["locked"] is True and state["device"]["idle_seconds"] == 321
    assert state["device"]["advertising"] is True and state["device"]["agent_version"] == "1.2.3"
    assert state["last_seen_at"] is not None


async def test_heartbeat_auth_failures(org, client):
    for headers in (
        {},
        {"Authorization": "Device "},
        {"Authorization": "Bearer abc"},
        {"Authorization": "Device adt_nope"},
    ):
        r = await client.post(f"{API}/desk/heartbeat", json={}, headers=headers)
        assert r.status_code == 401, headers
        assert r.json()["error"]["code"] == "device_not_paired"


async def test_inactive_device_cannot_heartbeat(org, client):
    d = await desk_app(client, org)
    _, r = await pair(client, org, d)
    await client.patch(
        f"{API}/places/desk-devices/{d['id']}", json={"active": False}, headers=org.owner_headers
    )
    hb = {"Authorization": f"Device {r.json()['device_token']}"}
    assert (await client.post(f"{API}/desk/heartbeat", json={}, headers=hb)).status_code == 401


async def test_devices_mine_wildcards_desk_apps_and_hides_others_devices(team_org, client):
    o = team_org
    await enable_desk_app(client, o)
    mk = lambda **kw: client.post(f"{API}/places/desk-devices", json=kw, headers=o.owner_headers)  # noqa: E731
    tag = (await mk(kind="tag", name="Shared tag", ibeacon_uuid=TAG_UUID, major=7, minor=8)).json()
    mine = (
        await mk(kind="desk_app", name="My desk", assigned_user_id=o.users["emp1"]["id"])
    ).json()
    theirs = (
        await mk(kind="desk_app", name="Their desk", assigned_user_id=o.users["emp2"]["id"])
    ).json()
    unpaired = (await mk(kind="desk_app", name="Unpaired")).json()
    for dev in (mine, theirs):
        _, r = await pair(client, o, dev)
        assert r.status_code == 200
    inactive = (await mk(kind="tag", name="Off", ibeacon_uuid=TAG_UUID, major=9, minor=9)).json()
    await client.patch(
        f"{API}/places/desk-devices/{inactive['id']}",
        json={"active": False},
        headers=o.owner_headers,
    )

    got = (await client.get(f"{API}/places/desk-devices/mine", headers=o.headers["emp1"])).json()
    by_name = {d["name"]: d for d in got}
    assert set(by_name) == {"Shared tag", "My desk"}, by_name.keys()
    assert (by_name["Shared tag"]["major"], by_name["Shared tag"]["minor"]) == (7, 8)
    assert by_name["My desk"]["major"] is None and by_name["My desk"]["minor"] is None
    assert by_name["My desk"]["ibeacon_uuid"] == o.signup["org"]["settings"]["desk_uuid"]
    assert theirs["id"] not in str(got) and unpaired["id"] not in str(got) and tag["id"] in str(got)


async def test_device_management_requires_places_manage(team_org, client):
    h = team_org.headers["boss"]
    assert (await client.get(f"{API}/places/desk-devices", headers=h)).status_code == 403
    r = await client.post(
        f"{API}/places/desk-devices",
        json={"kind": "tag", "name": "T", "ibeacon_uuid": TAG_UUID, "major": 1, "minor": 1},
        headers=h,
    )
    assert r.status_code == 403
    # but every signed in user can ask which devices to scan for
    assert (await client.get(f"{API}/places/desk-devices/mine", headers=h)).status_code == 200


async def test_devices_in_other_orgs_are_invisible_to_the_pairing_endpoints(make_org, client):
    a, b = await make_org("A Co"), await make_org("B Co")
    d = await desk_app(client, a)
    assert (
        await client.post(
            f"{API}/places/desk-devices/{d['id']}/pairing-code", headers=b.owner_headers
        )
    ).status_code == 404
    assert (
        await client.post(f"{API}/places/desk-devices/{d['id']}/unpair", headers=b.owner_headers)
    ).status_code == 404
    assert bearer(a.signup)  # silence unused import in some configurations
