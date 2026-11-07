from .conftest import (
    DESK_UUID,
    ZONE_UUID,
    auth,
    login,
    make_beacon,
    register,
)


def _make_manager(client, admin_token, office_id):
    register(client, office_id, "Manny", "manny@example.com")
    client.patch("/api/admin/users/EMP0001", headers=auth(admin_token), json={"role": "manager"})
    return login(client, "EMP0001", "password123")


def _other_office(client, admin_token):
    return client.post(
        "/api/offices",
        headers=auth(admin_token),
        json={"name": "Other", "latitude": 1, "longitude": 1, "radius": 100},
    ).json()["id"]


# ---------- CRUD ----------


def test_admin_crud(client, admin_token, office_id):
    created = make_beacon(client, admin_token, office_id, uuid=DESK_UUID.replace("-", ""))
    # uuid is normalised: lowercase, dashed
    assert created["uuid"] == DESK_UUID.lower()
    assert created["active"] is True and created["assigned_employee_id"] is None

    r = client.patch(
        f"/api/beacons/{created['id']}",
        headers=auth(admin_token),
        json={"name": "Desk 2", "minor": None, "active": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Desk 2" and body["minor"] is None and body["active"] is False
    assert body["major"] == 1  # untouched

    listed = client.get("/api/beacons", headers=auth(admin_token)).json()
    assert [b["id"] for b in listed] == [created["id"]]

    assert (
        client.delete(f"/api/beacons/{created['id']}", headers=auth(admin_token)).status_code == 204
    )
    assert (
        client.delete(f"/api/beacons/{created['id']}", headers=auth(admin_token)).status_code == 404
    )
    assert client.get("/api/beacons", headers=auth(admin_token)).json() == []


def test_unknown_beacon_and_bad_id(client, admin_token):
    r = client.patch("/api/beacons/000000000000000000000000", headers=auth(admin_token), json={})
    assert r.status_code == 404
    assert client.delete("/api/beacons/not-an-id", headers=auth(admin_token)).status_code == 404


def test_validation(client, admin_token, office_id):
    def post(**overrides):
        body = {"office_id": office_id, "name": "B", "uuid": DESK_UUID, "major": 1, "minor": 1}
        return client.post("/api/beacons", headers=auth(admin_token), json={**body, **overrides})

    assert post(uuid="not-a-uuid").status_code == 422
    assert post(major=-1).status_code == 422
    assert post(major=65536).status_code == 422
    assert post(minor=70000).status_code == 422
    assert post(major=65535, minor=0).status_code == 201
    assert post(name="").status_code == 422
    assert post(office_id="000000000000000000000000").status_code == 422
    assert post(assigned_employee_id="NOBODY").status_code == 422

    created = post(major=None, minor=None).json()
    for bad in ({"uuid": "xyz"}, {"major": 99999}, {"office_id": "0" * 24}, {"name": None}):
        r = client.patch(f"/api/beacons/{created['id']}", headers=auth(admin_token), json=bad)
        assert r.status_code == 422, bad
    r = client.patch(
        f"/api/beacons/{created['id']}",
        headers=auth(admin_token),
        json={"assigned_employee_id": "NOBODY"},
    )
    assert r.status_code == 422


def test_assign_employee_and_unassign(client, admin_token, office_id):
    register(client, office_id)
    beacon = make_beacon(client, admin_token, office_id, assigned_employee_id="EMP0001")
    assert beacon["assigned_employee_id"] == "EMP0001"
    r = client.patch(
        f"/api/beacons/{beacon['id']}",
        headers=auth(admin_token),
        json={"assigned_employee_id": None},
    )
    assert r.json()["assigned_employee_id"] is None


# ---------- permissions ----------


def test_only_admin_can_write(client, admin_token, office_id):
    manager = _make_manager(client, admin_token, office_id)
    employee = register(client, office_id, "Alice", "alice@example.com")["access_token"]
    beacon = make_beacon(client, admin_token, office_id)
    body = {"office_id": office_id, "name": "B", "uuid": DESK_UUID}
    for token in (manager, employee):
        assert client.post("/api/beacons", headers=auth(token), json=body).status_code == 403
        r = client.patch(f"/api/beacons/{beacon['id']}", headers=auth(token), json={"name": "x"})
        assert r.status_code == 403
        assert client.delete(f"/api/beacons/{beacon['id']}", headers=auth(token)).status_code == 403
    assert client.post("/api/beacons", json=body).status_code == 401


def test_employee_cannot_list_all(client, office_id):
    token = register(client, office_id)["access_token"]
    assert client.get("/api/beacons", headers=auth(token)).status_code == 403


def test_manager_sees_only_their_team_offices(client, admin_token, office_id):
    manager = _make_manager(client, admin_token, office_id)
    other = _other_office(client, admin_token)
    mine = make_beacon(client, admin_token, office_id, name="Mine")
    theirs = make_beacon(client, admin_token, other, name="Theirs")

    listed = client.get("/api/beacons", headers=auth(manager)).json()
    assert [b["id"] for b in listed] == [mine["id"]]
    assert client.get(f"/api/beacons?office_id={office_id}", headers=auth(manager)).json() == listed
    assert client.get(f"/api/beacons?office_id={other}", headers=auth(manager)).json() == []

    admin_all = client.get("/api/beacons", headers=auth(admin_token)).json()
    assert {b["id"] for b in admin_all} == {mine["id"], theirs["id"]}
    only_other = client.get(f"/api/beacons?office_id={other}", headers=auth(admin_token)).json()
    assert [b["id"] for b in only_other] == [theirs["id"]]


# ---------- /beacons/mine ----------


def test_mine_prefers_assigned_over_zone(client, admin_token, office_id):
    token = register(client, office_id)["access_token"]
    zone = make_beacon(client, admin_token, office_id, name="Zone", uuid=ZONE_UUID)
    # only the zone beacon exists -> that one
    assert [b["id"] for b in client.get("/api/beacons/mine", headers=auth(token)).json()] == [
        zone["id"]
    ]

    desk = make_beacon(client, admin_token, office_id, name="Desk", assigned_employee_id="EMP0001")
    assert [b["id"] for b in client.get("/api/beacons/mine", headers=auth(token)).json()] == [
        desk["id"]
    ]

    # inactive assigned beacon -> fall back to the zone again
    client.patch(f"/api/beacons/{desk['id']}", headers=auth(admin_token), json={"active": False})
    assert [b["id"] for b in client.get("/api/beacons/mine", headers=auth(token)).json()] == [
        zone["id"]
    ]


def test_mine_ignores_other_offices_and_other_employees(client, admin_token, office_id):
    token = register(client, office_id)["access_token"]
    other = _other_office(client, admin_token)
    register(client, office_id, "Bob", "bob@example.com")
    make_beacon(client, admin_token, other, name="Elsewhere", uuid=ZONE_UUID)
    make_beacon(client, admin_token, office_id, name="Bobs", assigned_employee_id="EMP0002")
    assert client.get("/api/beacons/mine", headers=auth(token)).json() == []


def test_mine_requires_auth(client):
    assert client.get("/api/beacons/mine").status_code == 401
