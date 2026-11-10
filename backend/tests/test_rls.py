"""Database-level tenant isolation, for every tenant table, as the real request-handling role."""

import re
from pathlib import Path

import asyncpg
import pytest

from .conftest import bearer

API = "/api/v1"
APP_PKG = Path(__file__).resolve().parent.parent / "app"


async def tenant_tables(conn) -> dict[str, str]:
    """{table: org column} for every public table that is tenant data (found dynamically so
    step 2's tables are covered automatically)."""
    rows = await conn.fetch(
        """SELECT c.relname, a.attname
           FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_attribute a ON a.attrelid = c.oid AND NOT a.attisdropped
           WHERE n.nspname = 'public' AND c.relkind = 'r'
             AND ((a.attname = 'org_id') OR (c.relname = 'organizations' AND a.attname = 'id'))
           ORDER BY c.relname"""
    )
    return {r["relname"]: r["attname"] for r in rows}


async def populate(client, org, mailbox):
    """Put at least one row into every tenant table of ``org`` through the real API."""
    h = org.owner_headers
    await client.post(f"{API}/departments", json={"name": "Dept"}, headers=h)
    await client.post(
        f"{API}/places/offices",
        json={"name": "HQ", "latitude": 1, "longitude": 2, "timezone": "UTC"},
        headers=h,
    )
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
    await client.post(
        f"{API}/people", json={"name": "Invitee", "email": f"i@{org.slug}.example.com"}, headers=h
    )
    me = (await client.get(f"{API}/people/me", headers=h)).json()
    await client.post(f"{API}/auth/consent", json={"version": me["consent_version"]}, headers=h)


async def insert_idempotency_row(session_factory, org_id):
    pass


@pytest.fixture
async def two_orgs(make_org, client, mailbox, app):
    a, b = await make_org("Alpha"), await make_org("Beta")
    for o in (a, b):
        await populate(client, o, mailbox)
        import uuid

        from app.core.idempotency import run_idempotent

        async with app.state.db.tenant_session(uuid.UUID(o.id)) as s:

            async def handler():
                return 200, {"ok": True}

            await run_idempotent(
                s,
                org_id=uuid.UUID(o.id),
                user_id=None,
                key="k1",
                scope="t",
                payload={},
                handler=handler,
            )
    return a, b


async def app_conn(cluster, org_id: str | None):
    """Connection as attendify_app, optionally inside a transaction with app.org_id set."""
    conn = await asyncpg.connect(cluster.app_url.replace("+asyncpg", ""))
    tx = conn.transaction()
    await tx.start()
    if org_id:
        await conn.execute(f"SET LOCAL app.org_id = '{org_id}'")
    return conn, tx


async def test_every_tenant_table_has_forced_rls_and_a_policy(cluster, owner_conn):
    tables = await tenant_tables(owner_conn)
    assert {
        "organizations",
        "users",
        "roles",
        "departments",
        "offices",
        "desk_devices",
        "auth_sessions",
        "auth_tokens",
        "consents",
        "audit_log",
        "idempotency_keys",
    } <= set(tables)
    for table in tables:
        flags = await owner_conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1", table
        )
        assert flags["relrowsecurity"] and flags["relforcerowsecurity"], table
        policies = await owner_conn.fetch(
            "SELECT qual, with_check FROM pg_policies WHERE tablename = $1", table
        )
        assert policies, table
        assert all(p["qual"] and p["with_check"] for p in policies), table


async def test_app_role_has_no_bypass_and_is_not_owner(owner_conn):
    app = await owner_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'attendify_app'"
    )
    sysrole = await owner_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'attendify_system'"
    )
    assert not app["rolsuper"] and not app["rolbypassrls"]
    assert sysrole["rolbypassrls"] and not sysrole["rolsuper"]
    owners = await owner_conn.fetch(
        "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'"
    )
    assert "attendify_app" not in {o["tableowner"] for o in owners}


async def test_no_tenant_context_means_no_rows_not_an_error(cluster, two_orgs):
    conn, tx = await app_conn(cluster, None)
    try:
        for table in await tenant_tables(conn):
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 0, table
    finally:
        await tx.rollback()
        await conn.close()


async def test_select_update_delete_see_only_own_org(cluster, two_orgs, owner_conn):
    a, b = two_orgs
    tables = await tenant_tables(owner_conn)
    for table, col in tables.items():
        own = await owner_conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {col} = $1::uuid", a.id
        )
        foreign = await owner_conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {col} = $1::uuid", b.id
        )
        assert own > 0 and foreign > 0, f"seed data missing for {table}"
        conn, tx = await app_conn(cluster, a.id)
        try:
            assert (
                await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {col} = $1::uuid", b.id)
                == 0
            ), table
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") == own, table
            # Raw SQL against the other org's rows touches nothing.
            if table not in ("audit_log",):
                updated = await conn.execute(
                    f"UPDATE {table} SET {col} = {col} WHERE {col} = $1::uuid", b.id
                )
                assert updated == "UPDATE 0", table
                if table != "organizations":  # no DELETE grant there
                    assert (
                        await conn.execute(f"DELETE FROM {table} WHERE {col} = $1::uuid", b.id)
                        == "DELETE 0"
                    ), table
        finally:
            await tx.rollback()
            await conn.close()
    # ...and nothing of org B was harmed.
    for table, col in tables.items():
        assert (
            await owner_conn.fetchval(f"SELECT count(*) FROM {table} WHERE {col} = $1::uuid", b.id)
            > 0
        )


async def test_insert_into_another_org_is_rejected(cluster, two_orgs, owner_conn):
    a, b = two_orgs
    for table, col in (await tenant_tables(owner_conn)).items():
        row = await owner_conn.fetchval(
            f"SELECT to_jsonb(t) FROM {table} t WHERE {col} = $1::uuid LIMIT 1", a.id
        )
        conn, tx = await app_conn(cluster, b.id)  # context is B, the row says A
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    f"INSERT INTO {table} SELECT * FROM jsonb_populate_record(NULL::{table}, $1::jsonb)",
                    row,
                )
        finally:
            await tx.rollback()
            await conn.close()


async def test_update_cannot_move_a_row_to_another_org(cluster, two_orgs, owner_conn):
    a, b = two_orgs
    conn, tx = await app_conn(cluster, a.id)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE departments SET org_id = $1::uuid", b.id)
    finally:
        await tx.rollback()
        await conn.close()


async def test_audit_log_is_append_only_even_for_the_owner(cluster, two_orgs, owner_conn):
    a, _ = two_orgs
    conn, tx = await app_conn(cluster, a.id)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE audit_log SET action = 'x'")
    finally:
        await tx.rollback()
        await conn.close()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await owner_conn.execute("DELETE FROM audit_log")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await owner_conn.execute("UPDATE audit_log SET action = 'x'")


async def test_cross_org_foreign_keys_are_impossible(owner_conn, two_orgs):
    a, b = two_orgs
    dept_b = await owner_conn.fetchval("SELECT id FROM departments WHERE org_id = $1::uuid", b.id)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner_conn.execute(
            "UPDATE users SET department_id = $1 WHERE org_id = $2::uuid", dept_b, a.id
        )


# ------------------------------------------------------------------------------- API level


async def test_org_a_admin_cannot_see_or_modify_org_b(make_org, client):
    a, b = await make_org("Alpha"), await make_org("Beta")
    await a.add_user("admin", role="admin")
    await b.add_user("victim")
    dept = (
        await client.post(f"{API}/departments", json={"name": "BDept"}, headers=b.owner_headers)
    ).json()
    office = (
        await client.post(
            f"{API}/places/offices",
            json={"name": "BHQ", "latitude": 1, "longitude": 2},
            headers=b.owner_headers,
        )
    ).json()
    device = (
        await client.post(
            f"{API}/places/desk-devices",
            json={
                "kind": "tag",
                "name": "T",
                "ibeacon_uuid": "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901",
                "major": 1,
                "minor": 1,
            },
            headers=b.owner_headers,
        )
    ).json()
    h = a.headers["admin"]
    victim = b.users["victim"]["id"]

    assert victim not in str((await client.get(f"{API}/people?limit=200", headers=h)).json())
    assert b.id not in str((await client.get(f"{API}/audit?limit=200", headers=h)).json())
    for method, url, body in [
        ("GET", f"/people/{victim}", None),
        ("PATCH", f"/people/{victim}", {"name": "Hacked"}),
        ("POST", f"/people/{victim}/disable", None),
        ("POST", f"/people/{victim}/enable", None),
        ("POST", f"/people/{victim}/invite", None),
        ("POST", f"/people/{victim}/revoke-sessions", None),
        ("GET", f"/people/{victim}/sessions", None),
        ("PATCH", f"/departments/{dept['id']}", {"name": "Hacked"}),
        ("DELETE", f"/departments/{dept['id']}", None),
        ("GET", f"/places/offices/{office['id']}", None),
        ("PATCH", f"/places/offices/{office['id']}", {"name": "Hacked"}),
        ("DELETE", f"/places/offices/{office['id']}", None),
        ("GET", f"/places/desk-devices/{device['id']}", None),
        ("PATCH", f"/places/desk-devices/{device['id']}", {"name": "Hacked"}),
        ("DELETE", f"/places/desk-devices/{device['id']}", None),
    ]:
        r = await client.request(method, f"{API}{url}", json=body, headers=h)
        assert r.status_code == 404, (method, url, r.status_code, r.text)

    assert [d["name"] for d in (await client.get(f"{API}/departments", headers=h)).json()] == []
    assert (await client.get(f"{API}/places/offices", headers=h)).json() == []
    assert (await client.get(f"{API}/places/desk-devices", headers=h)).json() == []
    # Creating with ids from the other org is refused.
    r = await client.post(
        f"{API}/people",
        json={"name": "X", "email": "x@a.example.com", "manager_id": victim},
        headers=h,
    )
    assert r.status_code == 400
    r = await client.post(
        f"{API}/people",
        json={"name": "X", "email": "x@a.example.com", "department_id": dept["id"]},
        headers=h,
    )
    assert r.status_code == 400
    # B is untouched.
    assert (await client.get(f"{API}/people/{victim}", headers=b.owner_headers)).json()[
        "name"
    ] == "Victim"
    assert (
        await client.get(f"{API}/places/offices/{office['id']}", headers=b.owner_headers)
    ).json()["name"] == "BHQ"


async def test_token_of_org_a_cannot_be_pointed_at_org_b(make_org, client):
    """An access token carries its org; forging another org id breaks the signature."""
    import jwt

    a, b = await make_org("Alpha"), await make_org("Beta")
    claims = jwt.decode(a.signup["access_token"], options={"verify_signature": False})
    claims["org"] = b.id
    forged = jwt.encode(claims, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    r = await client.get(f"{API}/people/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401
    # Even with the right signature, a user id that does not exist in the claimed org is rejected.
    import uuid

    from app.core.security import AccessClaims, create_access_token

    token, _ = create_access_token(
        client._transport.app.state.settings,
        AccessClaims(
            user_id=uuid.UUID(a.users["owner"]["id"]),
            org_id=uuid.UUID(b.id),
            session_id=uuid.uuid4(),
        ),
    )
    assert (
        await client.get(f"{API}/people/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 401
    assert bearer(a.signup)


# --------------------------------------------------------------- system session discipline


def test_system_session_is_only_used_by_the_named_modules():
    from app.core.db import SYSTEM_SESSION_ALLOWED

    pattern = re.compile(r"system_session|database_system_url|attendify_system")
    offenders = []
    for path in APP_PKG.rglob("*.py"):
        rel = path.relative_to(APP_PKG).as_posix()
        if not pattern.search(path.read_text()):
            continue
        if rel in ("core/config.py", "core/rls.py"):  # URL setting / role-name constant
            continue
        if not any(
            rel == a or (a.endswith("/") and rel.startswith(a)) for a in SYSTEM_SESSION_ALLOWED
        ):
            offenders.append(rel)
    assert not offenders, f"system (BYPASSRLS) session used outside its named modules: {offenders}"


def test_the_allowed_modules_really_exist_and_use_it():
    from app.core.db import SYSTEM_SESSION_ALLOWED

    users = [
        rel
        for rel in SYSTEM_SESSION_ALLOWED
        if not rel.endswith("/")
        and rel != "core/db.py"
        and "system_session" in (APP_PKG / rel).read_text()
    ]
    assert set(users) == {"tenancy/signup.py", "auth/preauth.py", "places/desk_system.py"}


def test_routers_never_touch_the_system_session_directly():
    for path in (APP_PKG / "api").rglob("*.py"):
        assert "system_session" not in path.read_text(), path
