import uuid

from .conftest import PASSWORD, bearer

API = "/api/v1"


async def test_signup_creates_org_owner_roles_and_defaults(client, owner_conn):
    r = await client.post(
        f"{API}/orgs/signup",
        json={
            "org_name": "Acme Widgets Ltd.",
            "name": "Ann",
            "email": "Ann@Acme.example.com",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["org"]["slug"] == "acme-widgets-ltd"
    assert body["user"]["email"] == "ann@acme.example.com" and body["user"]["role_key"] == "owner"
    assert body["user"]["employee_code"] == "E0001"
    modules = body["org"]["modules"]
    assert (
        modules["geofence"]
        and modules["face"]
        and not modules["desk_app"]
        and not modules["proximity"]
    )
    settings = body["org"]["settings"]
    uuid.UUID(settings["desk_uuid"])
    assert settings["plan"]["max_employees"] == 50
    roles = await owner_conn.fetch("SELECT key, permissions FROM roles ORDER BY key")
    assert [r["key"] for r in roles] == ["admin", "employee", "manager", "owner"]
    assert "org.manage" in dict(roles)["owner"] and "org.manage" not in dict(roles)["admin"]
    me = await client.get(f"{API}/people/me", headers=bearer(body))
    assert me.status_code == 200
    audit = await client.get(f"{API}/audit", headers=bearer(body))
    assert [i["action"] for i in audit.json()["items"]] == ["org.signup"]


async def test_each_org_gets_its_own_desk_uuid_and_unique_slug(make_org):
    a, b = await make_org("Same Name"), await make_org("Same Name")
    assert a.slug != b.slug and b.slug.startswith("same-name-")
    assert a.signup["org"]["settings"]["desk_uuid"] != b.signup["org"]["settings"]["desk_uuid"]


async def test_signup_duplicate_email(org, client):
    r = await client.post(
        f"{API}/orgs/signup",
        json={"org_name": "Other", "name": "X", "email": org.owner_email, "password": PASSWORD},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


async def test_signup_can_be_disabled(cluster):
    import httpx

    from app.main import create_app

    from .conftest import make_settings

    app = create_app(make_settings(cluster, allow_signup=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"{API}/orgs/signup",
            json={"org_name": "Other", "name": "X", "email": "x@example.com", "password": PASSWORD},
        )
    await app.state.db.dispose()
    assert r.status_code == 403 and r.json()["error"]["code"] == "signup_disabled"


async def test_signup_is_rate_limited_per_ip(cluster):
    import httpx

    from app.main import create_app

    from .conftest import make_settings

    app = create_app(make_settings(cluster, signup_max_per_hour=2))
    transport = httpx.ASGITransport(app=app, client=("198.51.100.7", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        codes = []
        for i in range(3):
            r = await c.post(
                f"{API}/orgs/signup",
                json={
                    "org_name": f"Org {i}",
                    "name": "X",
                    "email": f"x{i}@example.com",
                    "password": PASSWORD,
                },
            )
            codes.append(r.status_code)
    await app.state.db.dispose()
    assert codes == [201, 201, 429]


async def test_get_and_patch_org_need_org_manage(team_org, client):
    assert (await client.get(f"{API}/org", headers=team_org.owner_headers)).status_code == 200
    for who in ("admin", "boss", "emp1"):
        assert (await client.get(f"{API}/org", headers=team_org.headers[who])).status_code == 403
        r = await client.patch(f"{API}/org", json={"name": "Hax"}, headers=team_org.headers[who])
        assert r.status_code == 403


async def test_patch_org_modules_settings_and_audit(org, client):
    h = org.owner_headers
    r = await client.patch(
        f"{API}/org",
        json={
            "modules": {"face": False, "wfh": True},
            "settings": {"block_rooted_devices": False},
            "name": "Renamed Co",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modules"]["face"] is False and body["modules"]["wfh"] is True
    assert body["modules"]["geofence"] is True  # untouched
    assert body["settings"]["block_rooted_devices"] is False and body["name"] == "Renamed Co"
    audit = (await client.get(f"{API}/audit?action=org.update", headers=h)).json()["items"]
    assert (
        audit[0]["before"]["modules"]["face"] is True
        and audit[0]["after"]["modules"]["face"] is False
    )


async def test_patch_org_rejects_unknown_or_protected_values(org, client):
    h = org.owner_headers
    for payload in (
        {"modules": {"telepathy": True}},
        {"settings": {"desk_uuid": str(uuid.uuid4())}},
        {"settings": {"plan": {"max_employees": 99999}}},
        {"settings": {"presence_retention_days": 0}},
    ):
        r = await client.patch(f"{API}/org", json=payload, headers=h)
        assert r.status_code == 400, payload
    unchanged = (await client.get(f"{API}/org", headers=h)).json()
    assert unchanged["settings"]["plan"]["max_employees"] == 50


async def test_plan_limit_max_employees_is_enforced(cluster):
    import httpx

    from app.main import create_app

    from .conftest import make_settings

    app = create_app(make_settings(cluster, default_max_employees=3))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"{API}/orgs/signup",
            json={
                "org_name": "Tiny",
                "name": "T",
                "email": "t@tiny.example.com",
                "password": PASSWORD,
            },
        )
        h = bearer(r.json())
        created = []
        for i in range(3):
            resp = await c.post(
                f"{API}/people",
                json={"name": f"U{i}", "email": f"u{i}@tiny.example.com"},
                headers=h,
            )
            created.append(resp)
        assert [x.status_code for x in created] == [201, 201, 403]
        assert created[2].json()["error"]["code"] == "plan_limit_reached"
        # Disabling frees a seat; enabling again needs one.
        await c.post(f"{API}/people/{created[0].json()['id']}/disable", headers=h)
        again = await c.post(
            f"{API}/people", json={"name": "U9", "email": "u9@tiny.example.com"}, headers=h
        )
        assert again.status_code == 201
        blocked = await c.post(f"{API}/people/{created[0].json()['id']}/enable", headers=h)
        assert blocked.status_code == 403
    await app.state.db.dispose()
