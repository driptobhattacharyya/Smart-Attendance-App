"""Permission matrix: built-in roles x representative endpoints, plus 'no secrets in responses'."""

import pytest

API = "/api/v1"
TAG = {"kind": "tag", "name": "T", "ibeacon_uuid": "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"}
FORBIDDEN_FRAGMENTS = ("_hash", "password", "secret", "mfa_", "token_hash", "pairing_code")

ALL = {"owner", "admin", "manager", "employee"}
MANAGE = {"owner", "admin"}
OWNER = {"owner"}
TEAM_VIEW = {"owner", "admin", "manager"}

# (method, path, body, roles that are allowed)
MATRIX = [
    ("GET", "/people/me", None, ALL),
    ("GET", "/people", None, TEAM_VIEW),
    ("GET", "/people/{emp}", None, ALL),  # own record is always readable; scope checked elsewhere
    ("POST", "/people", {"name": "N", "email": "n{role}@example.com"}, MANAGE),
    ("PATCH", "/people/{emp}", {"phone": "123"}, MANAGE),
    ("GET", "/departments", None, ALL),
    ("POST", "/departments", {"name": "D-{role}"}, MANAGE),
    ("GET", "/places/offices", None, ALL),
    ("POST", "/places/offices", {"name": "O-{role}", "latitude": 1, "longitude": 1}, MANAGE),
    ("GET", "/places/desk-devices/mine", None, ALL),
    ("GET", "/places/desk-devices", None, MANAGE),
    ("POST", "/places/desk-devices", {**TAG, "major": 1, "minor": 2}, MANAGE),
    ("GET", "/audit", None, MANAGE),
    ("GET", "/org", None, OWNER),
    ("PATCH", "/org", {"name": "Renamed Co"}, OWNER),
    ("GET", "/auth/sessions", None, ALL),
    ("GET", "/consent/text", None, ALL),
    ("POST", "/auth/consent/withdraw", None, ALL),
]


def assert_no_secrets(value, where=""):
    text = str(value).lower()
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in text, f"{fragment!r} leaked in {where}"


@pytest.mark.parametrize("role", ["owner", "admin", "manager", "employee"])
async def test_permission_matrix(team_org, client, role):
    key = {"manager": "boss", "employee": "emp1"}.get(role, role)
    headers = team_org.headers[key]
    emp = team_org.users["emp1"]["id"]  # employee: own record; manager boss: in the tree
    for method, path, body, allowed in MATRIX:
        url = API + path.replace("{emp}", emp)
        payload = None
        if body:
            payload = {
                k: (v.replace("{role}", role) if isinstance(v, str) else v) for k, v in body.items()
            }
            if path == "/places/desk-devices":
                payload = {**payload, "minor": 2 + len(role)}
        r = await client.request(method, url, json=payload, headers=headers)
        label = f"{role} {method} {path} -> {r.status_code} {r.text[:120]}"
        if role in allowed:
            assert r.status_code < 400, label
        else:
            assert r.status_code == 403, label
            assert r.json()["error"]["code"] == "forbidden"
        if r.headers.get("content-type", "").startswith("application/json"):
            assert_no_secrets(r.json(), label)


async def test_unauthenticated_requests_are_rejected_everywhere(team_org, client):
    for method, path, body, _ in MATRIX:
        r = await client.request(
            method, API + path.replace("{emp}", team_org.users["emp1"]["id"]), json=body
        )
        assert r.status_code == 401, (method, path)
        assert r.headers["www-authenticate"] == "Bearer"


async def test_builtin_roles_match_the_architecture_table(team_org, client):
    expected = {
        "owner": {
            "attendance.self",
            "attendance.view.team",
            "attendance.view.org",
            "requests.approve.team",
            "requests.approve.org",
            "attendance.correct",
            "people.view.team",
            "people.manage",
            "places.manage",
            "policies.manage",
            "reports.export",
            "audit.view",
            "org.manage",
        },
        "manager": {
            "attendance.self",
            "attendance.view.team",
            "requests.approve.team",
            "people.view.team",
            "reports.export",
        },
        "employee": {"attendance.self"},
    }
    expected["admin"] = expected["owner"] - {"org.manage"}
    for role, key in (
        ("owner", "owner"),
        ("admin", "admin"),
        ("manager", "boss"),
        ("employee", "emp1"),
    ):
        me = (await client.get(f"{API}/people/me", headers=team_org.headers[key])).json()
        assert set(me["permissions"]) == expected[role], role


async def test_unknown_permissions_cannot_be_required():
    from app.rbac.deps import require_permission

    with pytest.raises(ValueError):
        require_permission("people.fly")


async def test_removed_permission_takes_effect_immediately(team_org, client, owner_conn):
    h = team_org.headers["admin"]
    assert (await client.get(f"{API}/audit", headers=h)).status_code == 200
    await owner_conn.execute(
        "UPDATE roles SET permissions = array_remove(permissions, 'audit.view') WHERE key = 'admin'"
    )
    assert (await client.get(f"{API}/audit", headers=h)).status_code == 403  # nothing cached


async def test_responses_never_contain_credentials(team_org, client, mailbox):
    """Walk the read endpoints as owner and scan everything that comes back."""
    o = team_org
    dev = (
        await client.post(
            f"{API}/places/desk-devices",
            json={
                "kind": "tag",
                "name": "T",
                "ibeacon_uuid": TAG["ibeacon_uuid"],
                "major": 1,
                "minor": 1,
            },
            headers=o.owner_headers,
        )
    ).json()
    paths = [
        "/people?limit=200",
        f"/people/{o.users['emp1']['id']}",
        "/people/me",
        f"/people/{o.users['emp1']['id']}/sessions",
        "/departments",
        "/places/offices",
        "/places/desk-devices",
        f"/places/desk-devices/{dev['id']}",
        "/places/desk-devices/mine",
        "/org",
        "/audit?limit=200",
        "/auth/sessions",
        "/consent/text",
    ]
    for p in paths:
        r = await client.get(API + p, headers=o.owner_headers)
        assert r.status_code == 200, p
        assert_no_secrets(r.json(), p)
    # login / signup responses carry tokens by design but never hashes
    assert "password_hash" not in str(o.tokens["owner"]) and "token_hash" not in str(
        o.tokens["owner"]
    )
    # audit entries recorded for password changes etc. carry no credentials either
    audit = (await client.get(f"{API}/audit?limit=200", headers=o.owner_headers)).json()
    assert_no_secrets([(i["before"], i["after"]) for i in audit["items"]], "audit")
