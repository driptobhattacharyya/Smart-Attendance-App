from .conftest import bearer

API = "/api/v1"


async def audit(client, org, qs=""):
    r = await client.get(f"{API}/audit?{qs}", headers=org.owner_headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_every_mutation_in_scope_is_audited(org, client):
    h = org.owner_headers
    dept = (await client.post(f"{API}/departments", json={"name": "D"}, headers=h)).json()
    await client.patch(f"{API}/departments/{dept['id']}", json={"name": "D2"}, headers=h)
    await client.delete(f"{API}/departments/{dept['id']}", headers=h)
    office = (
        await client.post(
            f"{API}/places/offices", json={"name": "O", "latitude": 1, "longitude": 1}, headers=h
        )
    ).json()
    await client.patch(f"{API}/places/offices/{office['id']}", json={"radius_m": 99}, headers=h)
    await client.delete(f"{API}/places/offices/{office['id']}", headers=h)
    dev = (
        await client.post(
            f"{API}/places/desk-devices",
            json={
                "kind": "tag",
                "name": "T",
                "ibeacon_uuid": "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901",
                "major": 1,
                "minor": 1,
            },
            headers=h,
        )
    ).json()
    await client.patch(f"{API}/places/desk-devices/{dev['id']}", json={"name": "T2"}, headers=h)
    await client.delete(f"{API}/places/desk-devices/{dev['id']}", headers=h)
    user = (
        await client.post(f"{API}/people", json={"name": "U", "email": "u@example.com"}, headers=h)
    ).json()
    await client.patch(f"{API}/people/{user['id']}", json={"name": "U2"}, headers=h)
    await client.post(f"{API}/people/{user['id']}/disable", headers=h)
    await client.post(f"{API}/people/{user['id']}/enable", headers=h)
    await client.post(f"{API}/people/{user['id']}/revoke-sessions", headers=h)
    await client.patch(f"{API}/org", json={"modules": {"wfh": True}}, headers=h)
    await client.post(f"{API}/auth/consent", json={"version": 1}, headers=h)
    await client.post(f"{API}/auth/consent/withdraw", headers=h)
    await client.post(f"{API}/auth/logout-all", headers=h)

    actions = {i["action"] for i in (await _all_actions(client, org))}
    expected = {
        "org.signup",
        "department.create",
        "department.update",
        "department.delete",
        "office.create",
        "office.update",
        "office.delete",
        "device.create",
        "device.update",
        "device.delete",
        "people.create",
        "people.update",
        "people.disable",
        "people.enable",
        "people.invite_resent",
        "people.sessions_revoked",
        "org.update",
        "consent.accept",
        "consent.withdraw",
        "auth.logout_all",
    }
    # enable() re-sends an invite for never-activated users, so that action appears too.
    assert expected - {"people.invite_resent"} <= actions, expected - actions


async def _all_actions(client, org):
    # logout-all killed the owner's token: read the trail through a fresh login
    login = await client.post(
        f"{API}/auth/login", json={"email": org.owner_email, "password": org.password}
    )
    r = await client.get(f"{API}/audit?limit=200", headers=bearer(login.json()))
    return r.json()["items"]


async def test_audit_entry_has_actor_before_after_ip_and_never_credentials(org, client):
    h = org.owner_headers
    u = (
        await client.post(f"{API}/people", json={"name": "U", "email": "u@example.com"}, headers=h)
    ).json()
    await client.patch(
        f"{API}/people/{u['id']}",
        json={"name": "Renamed", "phone": "555"},
        headers={**h, "User-Agent": "pytest-agent"},
    )
    item = (await audit(client, org, "action=people.update"))["items"][0]
    assert item["actor_user_id"] == org.users["owner"]["id"] and item["actor_kind"] == "user"
    assert item["target_type"] == "user" and item["target_id"] == u["id"]
    assert (
        item["before"]["name"] == "U"
        and item["after"]["name"] == "Renamed"
        and item["after"]["phone"] == "555"
    )
    assert item["ip"] == "127.0.0.1"
    assert "password" not in str(item) and "hash" not in str(item)


async def test_audit_filters_and_pagination(team_org, client):
    o = team_org
    h = o.owner_headers
    total = (await client.get(f"{API}/audit?limit=200", headers=h)).json()["items"]
    assert len(total) >= 8
    # newest first
    stamps = [i["at"] for i in total]
    assert stamps == sorted(stamps, reverse=True)

    by_prefix = (await client.get(f"{API}/audit?action=people.&limit=200", headers=h)).json()[
        "items"
    ]
    assert by_prefix and all(i["action"].startswith("people.") for i in by_prefix)
    exact = (await client.get(f"{API}/audit?action=people.create&limit=200", headers=h)).json()[
        "items"
    ]
    assert len(exact) == len(o.users) - 1
    target = o.users["emp1"]["id"]
    one = (await client.get(f"{API}/audit?target_type=user&target_id={target}", headers=h)).json()[
        "items"
    ]
    assert one and all(i["target_id"] == target for i in one)
    actor = (
        await client.get(f"{API}/audit?actor_user_id={o.users['emp1']['id']}", headers=h)
    ).json()["items"]
    assert (
        all(i["actor_user_id"] == o.users["emp1"]["id"] for i in actor) and actor
    )  # invite accepted
    future = (await client.get(f"{API}/audit?since=2999-01-01T00:00:00Z", headers=h)).json()[
        "items"
    ]
    assert future == []
    past = (await client.get(f"{API}/audit?until=2000-01-01T00:00:00Z", headers=h)).json()["items"]
    assert past == []

    seen, cursor = [], None
    while True:
        qs = "limit=4" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(f"{API}/audit?{qs}", headers=h)).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == len(total)
    assert seen == [i["id"] for i in total]


async def test_audit_requires_permission(team_org, client):
    for who in ("boss", "emp1"):
        assert (await client.get(f"{API}/audit", headers=team_org.headers[who])).status_code == 403
    assert (await client.get(f"{API}/audit", headers=team_org.headers["admin"])).status_code == 200


async def test_failed_attempts_and_reuse_detection_leave_audit_trail(org, client):
    t = (
        await client.post(
            f"{API}/auth/login", json={"email": org.owner_email, "password": org.password}
        )
    ).json()
    r1 = (
        await client.post(f"{API}/auth/refresh", json={"refresh_token": t["refresh_token"]})
    ).json()
    await client.post(f"{API}/auth/refresh", json={"refresh_token": t["refresh_token"]})
    login = (
        await client.post(
            f"{API}/auth/login", json={"email": org.owner_email, "password": org.password}
        )
    ).json()
    actions = [
        i["action"]
        for i in (await client.get(f"{API}/audit?limit=100", headers=bearer(login))).json()["items"]
    ]
    assert "auth.refresh_reuse_detected" in actions and actions.count("auth.login") == 2
    assert r1


# ------------------------------------------------------------------------------ consent


async def test_consent_text_is_rendered_per_org(make_org, client):
    a, b = await make_org("Alpha"), await make_org("Beta")
    ta = (await client.get(f"{API}/consent/text", headers=a.owner_headers)).json()
    assert ta["version"] == 1 and ta["accepted"] is False
    assert "90 days" in ta["body_markdown"] and "Attendify Desk app" not in ta["body_markdown"]
    assert "{" not in ta["body_markdown"]
    await client.patch(
        f"{API}/org",
        json={"modules": {"desk_app": True}, "settings": {"presence_retention_days": 30}},
        headers=b.owner_headers,
    )
    tb = (await client.get(f"{API}/consent/text", headers=b.owner_headers)).json()
    assert "30 days" in tb["body_markdown"] and "Attendify Desk app" in tb["body_markdown"]
    assert (await client.get(f"{API}/consent/text")).status_code == 401


async def test_consent_accept_withdraw_and_version_bump(org, client):
    h = org.owner_headers
    me = lambda: client.get(f"{API}/people/me", headers=h)  # noqa: E731
    assert (await me()).json()["consent_required"] is True
    wrong = await client.post(f"{API}/auth/consent", json={"version": 7}, headers=h)
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "consent_version_mismatch"
    assert (
        await client.post(f"{API}/auth/consent", json={"version": 1}, headers=h)
    ).status_code == 200
    assert (
        await client.post(f"{API}/auth/consent", json={"version": 1}, headers=h)
    ).status_code == 200  # idempotent
    assert (await me()).json()["consent_required"] is False
    login = await client.post(
        f"{API}/auth/login", json={"email": org.owner_email, "password": org.password}
    )
    assert login.json()["consent_required"] is False
    assert (await client.get(f"{API}/consent/text", headers=h)).json()["accepted"] is True

    await client.post(f"{API}/auth/consent/withdraw", headers=h)
    assert (await me()).json()["consent_required"] is True
    await client.post(f"{API}/auth/consent", json={"version": 1}, headers=h)
    assert (await me()).json()["consent_required"] is False

    bumped = await client.patch(f"{API}/org", json={"bump_consent_version": True}, headers=h)
    assert bumped.json()["consent_version"] == 2
    assert (await me()).json()["consent_required"] is True
    assert (
        await client.post(f"{API}/auth/consent", json={"version": 2}, headers=h)
    ).status_code == 200
    assert (await me()).json()["consent_required"] is False


async def test_consent_is_per_user_and_per_org(make_org, client):
    a, b = await make_org("Alpha"), await make_org("Beta")
    await a.add_user("e1")
    await client.post(f"{API}/auth/consent", json={"version": 1}, headers=a.owner_headers)
    assert (await client.get(f"{API}/people/me", headers=a.headers["e1"])).json()[
        "consent_required"
    ] is True
    assert (await client.get(f"{API}/people/me", headers=b.owner_headers)).json()[
        "consent_required"
    ] is True


async def test_require_consent_dependency_blocks_until_accepted(app, org, client):
    from fastapi import Depends

    from app.auth.deps import require_consent

    @app.get("/api/v1/_probe")
    async def probe(ctx=Depends(require_consent)):
        return {"ok": True}

    r = await client.get(f"{API}/_probe", headers=org.owner_headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "consent_required"
    await client.post(f"{API}/auth/consent", json={"version": 1}, headers=org.owner_headers)
    assert (await client.get(f"{API}/_probe", headers=org.owner_headers)).status_code == 200
    await client.post(f"{API}/auth/consent/withdraw", headers=org.owner_headers)
    assert (await client.get(f"{API}/_probe", headers=org.owner_headers)).status_code == 403
