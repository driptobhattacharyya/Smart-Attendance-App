from .conftest import PASSWORD

API = "/api/v1"


def ids(items):
    return {i["id"] for i in items}


async def test_managers_see_only_their_reporting_tree(team_org, client):
    u = team_org.users
    boss = (await client.get(f"{API}/people", headers=team_org.headers["boss"])).json()
    assert ids(boss["items"]) == {u[k]["id"] for k in ("lead", "emp1", "emp2", "direct")}
    lead = (await client.get(f"{API}/people", headers=team_org.headers["lead"])).json()
    assert ids(lead["items"]) == {u[k]["id"] for k in ("emp1", "emp2")}


async def test_manager_cannot_read_people_outside_the_tree(team_org, client):
    u, h = team_org.users, team_org.headers["lead"]
    assert (await client.get(f"{API}/people/{u['emp1']['id']}", headers=h)).status_code == 200
    for outside in ("boss", "direct", "outsider", "owner", "admin"):
        r = await client.get(f"{API}/people/{u[outside]['id']}", headers=h)
        assert r.status_code == 404, outside
    # ...but always their own record.
    assert (await client.get(f"{API}/people/{u['lead']['id']}", headers=h)).status_code == 200


async def test_deep_tree_is_followed_to_any_depth(make_org, client):
    o = await make_org("Deep")
    await o.add_user("m0", role="manager")
    prev = "m0"
    for i in range(1, 6):
        await o.add_user(f"m{i}", role="manager", manager=prev)
        prev = f"m{i}"
    await o.add_user("leaf", manager=prev)
    top = (await client.get(f"{API}/people", headers=o.headers["m0"])).json()["items"]
    assert len(top) == 6  # m1..m5 + leaf
    assert o.users["leaf"]["id"] in ids(top)
    assert (
        await client.get(f"{API}/people/{o.users['m0']['id']}", headers=o.headers["m5"])
    ).status_code == 404


async def test_employee_cannot_list_people(team_org, client):
    assert (await client.get(f"{API}/people", headers=team_org.headers["emp1"])).status_code == 403


async def test_admin_lists_everyone_with_pagination_and_search(team_org, client):
    h = team_org.headers["admin"]
    first = (await client.get(f"{API}/people?limit=3", headers=h)).json()
    assert len(first["items"]) == 3 and first["next_cursor"]
    seen = ids(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        page = (await client.get(f"{API}/people?limit=3&cursor={cursor}", headers=h)).json()
        assert not (seen & ids(page["items"]))
        seen |= ids(page["items"])
        cursor = page["next_cursor"]
    assert len(seen) == len(team_org.users)
    names = [
        i["name"].lower()
        for i in (await client.get(f"{API}/people?limit=50", headers=h)).json()["items"]
    ]
    assert names == sorted(names)

    found = (await client.get(f"{API}/people?q=EMP1", headers=h)).json()["items"]
    assert [i["name"] for i in found] == ["Emp1"]
    assert (await client.get(f"{API}/people?q=%25", headers=h)).json()["items"] == []  # literal %
    only_mgr = (await client.get(f"{API}/people?role_key=manager", headers=h)).json()["items"]
    assert {i["name"] for i in only_mgr} == {"Boss", "Lead"}


async def test_bad_cursor_and_limit(team_org, client):
    h = team_org.headers["admin"]
    r = await client.get(f"{API}/people?cursor=garbage", headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cursor"
    assert (await client.get(f"{API}/people?limit=0", headers=h)).status_code == 422


async def test_people_me_has_permissions_org_and_consent(team_org, client):
    me = (await client.get(f"{API}/people/me", headers=team_org.headers["boss"])).json()
    assert "people.view.team" in me["permissions"] and "people.manage" not in me["permissions"]
    assert me["org"]["slug"] == team_org.slug and "face" in me["org"]["modules"]
    assert me["consent_required"] is True and me["consent_version"] == 1
    patched = await client.patch(
        f"{API}/people/me", json={"name": "Boss Man"}, headers=team_org.headers["boss"]
    )
    assert patched.json()["name"] == "Boss Man"


async def test_create_validates_references_and_conflicts(org, client):
    h = org.owner_headers
    base = {"name": "Pat", "email": "pat@example.com"}
    bad_role = await client.post(f"{API}/people", json={**base, "role_key": "godmode"}, headers=h)
    assert bad_role.status_code == 400 and bad_role.json()["error"]["code"] == "unknown_role"
    ghost = "00000000-0000-0000-0000-000000000001"
    for field, code in (
        ("manager_id", "unknown_manager"),
        ("department_id", "unknown_department"),
        ("office_id", "unknown_office"),
    ):
        r = await client.post(f"{API}/people", json={**base, field: ghost}, headers=h)
        assert r.json()["error"]["code"] == code
    ok = await client.post(f"{API}/people", json=base, headers=h)
    assert ok.status_code == 201 and ok.json()["employee_code"] == "E0002"
    dup = await client.post(f"{API}/people", json={**base, "name": "Other"}, headers=h)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "email_taken"
    dup2 = await client.post(
        f"{API}/people",
        json={"name": "Q", "email": "q@example.com", "employee_code": "E0002"},
        headers=h,
    )
    assert dup2.json()["error"]["code"] == "employee_code_taken"


async def test_email_unique_across_organizations(make_org, client):
    a, b = await make_org("A Co"), await make_org("B Co")
    r = await client.post(
        f"{API}/people", json={"name": "X", "email": a.owner_email}, headers=b.owner_headers
    )
    assert r.status_code == 409


async def test_update_person_reporting_line_department_office(team_org, client):
    h = team_org.owner_headers
    dept = (await client.post(f"{API}/departments", json={"name": "Engineering"}, headers=h)).json()
    office = (
        await client.post(
            f"{API}/places/offices",
            json={"name": "HQ", "latitude": 1.0, "longitude": 2.0, "timezone": "Asia/Kolkata"},
            headers=h,
        )
    ).json()
    u = team_org.users
    r = await client.patch(
        f"{API}/people/{u['emp1']['id']}",
        json={
            "manager_id": u["direct"]["id"],
            "department_id": dept["id"],
            "office_id": office["id"],
            "role_key": "manager",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["manager_id"] == u["direct"]["id"] and body["role_key"] == "manager"
    # clearing the manager
    cleared = await client.patch(
        f"{API}/people/{u['emp1']['id']}", json={"manager_id": None}, headers=h
    )
    assert cleared.json()["manager_id"] is None and cleared.json()["department_id"] == dept["id"]
    # promoted role: old sessions were revoked
    assert (
        await client.get(f"{API}/people/me", headers=team_org.headers["emp1"])
    ).status_code == 401


async def test_reporting_cycles_are_rejected(team_org, client):
    u, h = team_org.users, team_org.owner_headers
    r = await client.patch(
        f"{API}/people/{u['boss']['id']}", json={"manager_id": u["emp1"]["id"]}, headers=h
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "reporting_cycle"
    r = await client.patch(
        f"{API}/people/{u['boss']['id']}", json={"manager_id": u["boss"]["id"]}, headers=h
    )
    assert r.json()["error"]["code"] == "invalid_manager"


async def test_admin_cannot_touch_owner_or_grant_owner(team_org, client):
    u, h = team_org.users, team_org.headers["admin"]
    assert (
        await client.patch(f"{API}/people/{u['owner']['id']}", json={"name": "Hax"}, headers=h)
    ).status_code == 403
    assert (
        await client.patch(f"{API}/people/{u['emp1']['id']}", json={"role_key": "owner"}, headers=h)
    ).status_code == 403
    assert (
        await client.post(f"{API}/people/{u['owner']['id']}/disable", headers=h)
    ).status_code == 403


async def test_last_owner_cannot_be_removed(org, client):
    owner_id = org.users["owner"]["id"]
    r = await client.patch(
        f"{API}/people/{owner_id}", json={"role_key": "admin"}, headers=org.owner_headers
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_owner"
    assert (
        await client.post(f"{API}/people/{owner_id}/disable", headers=org.owner_headers)
    ).status_code == 409  # self
    # a second owner makes it possible
    other = await org.add_user("co", role="owner")
    r = await client.patch(
        f"{API}/people/{owner_id}", json={"role_key": "admin"}, headers=org.headers["co"]
    )
    assert r.status_code == 200
    r = await client.post(f"{API}/people/{other['id']}/disable", headers=org.headers["co"])
    assert r.status_code == 409  # now co is the last owner (and it is themself)


async def test_departments_crud(org, client):
    h = org.owner_headers
    d = (await client.post(f"{API}/departments", json={"name": "Sales"}, headers=h)).json()
    assert (
        await client.post(f"{API}/departments", json={"name": "sales"}, headers=h)
    ).status_code == 409
    assert (
        await client.patch(f"{API}/departments/{d['id']}", json={"name": "Revenue"}, headers=h)
    ).json()["name"] == "Revenue"
    user = await client.post(
        f"{API}/people",
        json={"name": "S", "email": "s@example.com", "department_id": d["id"]},
        headers=h,
    )
    assert (await client.delete(f"{API}/departments/{d['id']}", headers=h)).status_code == 204
    assert (await client.get(f"{API}/people/{user.json()['id']}", headers=h)).json()[
        "department_id"
    ] is None
    assert (await client.get(f"{API}/departments", headers=h)).json() == []
    assert (await client.delete(f"{API}/departments/{d['id']}", headers=h)).status_code == 404


async def test_employee_can_list_departments_but_not_change_them(team_org, client):
    h = team_org.headers["emp1"]
    assert (await client.get(f"{API}/departments", headers=h)).status_code == 200
    assert (
        await client.post(f"{API}/departments", json={"name": "X"}, headers=h)
    ).status_code == 403


async def test_invited_users_can_be_enabled_back_to_invited(org, client, mailbox):
    r = await client.post(
        f"{API}/people", json={"name": "Ivy", "email": "ivy@example.com"}, headers=org.owner_headers
    )
    uid = r.json()["id"]
    await client.post(f"{API}/people/{uid}/disable", headers=org.owner_headers)
    back = await client.post(f"{API}/people/{uid}/enable", headers=org.owner_headers)
    assert back.json()["status"] == "invited"
    assert len(mailbox.to("ivy@example.com")) == 2
    assert PASSWORD  # the invite can be accepted afterwards (covered in test_auth)
