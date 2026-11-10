import re

import pytest

from app.main import create_app

from .conftest import PASSWORD, bearer, invite_token, make_settings

API = "/api/v1"


async def login(client, email, password=PASSWORD, **extra):
    return await client.post(
        f"{API}/auth/login", json={"email": email, "password": password, **extra}
    )


async def test_login_by_email_is_case_insensitive(org, client):
    r = await login(client, org.owner_email.upper())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert body["user"]["role_key"] == "owner"
    assert body["consent_required"] is True
    me = await client.get(f"{API}/people/me", headers=bearer(body))
    assert me.status_code == 200


async def test_login_by_org_slug_and_employee_code(org, client):
    r = await client.post(
        f"{API}/auth/login",
        json={"org_slug": org.slug.upper(), "employee_code": "E0001", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == org.owner_email


async def test_same_employee_code_in_two_orgs_is_not_ambiguous(make_org, client):
    a, b = await make_org("Alpha"), await make_org("Beta")
    await a.add_user("sam")
    await b.add_user("sam")
    r = await client.post(
        f"{API}/auth/login",
        json={"org_slug": b.slug, "employee_code": "E0002", "password": PASSWORD},
    )
    assert r.status_code == 200
    assert r.json()["user"]["id"] == b.users["sam"]["id"]


async def test_login_requires_an_identity(client):
    r = await client.post(f"{API}/auth/login", json={"password": "x"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_wrong_password_and_unknown_user_look_the_same(org, client):
    bad_pw = await login(client, org.owner_email, "wrong-password!!")
    unknown = await login(client, "nobody@example.com")
    for r in (bad_pw, unknown):
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_credentials"


async def test_login_rate_limit_per_email_then_429_even_with_right_password(org, app, client):
    for _ in range(10):
        assert (await login(client, org.owner_email, "wrong-password!!")).status_code == 401
    blocked = await login(client, org.owner_email)  # correct password, still blocked
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert "retry-after" in blocked.headers


async def test_login_rate_limit_per_ip(cluster, org):
    app = create_app(make_settings(cluster, login_max_failures=3))
    import httpx

    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        for i in range(3):
            r = await login(c, f"user{i}@example.com", "wrong-password!!")
            assert r.status_code == 401
        r = await login(c, "fresh@example.com", "wrong-password!!")
        assert r.status_code == 429
    await app.state.db.dispose()


async def test_successful_login_clears_email_failures(org, client, owner_conn):
    for _ in range(5):
        await login(client, org.owner_email, "wrong-password!!")
    count = "SELECT count(*) FROM auth_attempts WHERE key LIKE 'email:%'"
    assert await owner_conn.fetchval(count) == 5
    assert (await login(client, org.owner_email)).status_code == 200
    assert await owner_conn.fetchval(count) == 0


async def test_refresh_rotates_and_old_token_is_rejected(org, client):
    first = (await login(client, org.owner_email)).json()
    r = await client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (await client.get(f"{API}/people/me", headers=bearer(second))).status_code == 200
    # The old access token's session (the family) is still the same session.
    assert (await client.get(f"{API}/people/me", headers=bearer(first))).status_code == 200


async def test_refresh_token_reuse_revokes_the_whole_family(org, client):
    first = (await login(client, org.owner_email)).json()
    second = (
        await client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).json()
    third = (
        await client.post(f"{API}/auth/refresh", json={"refresh_token": second["refresh_token"]})
    ).json()
    # An attacker replays the first (already rotated) token.
    replay = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "token_reuse_detected"
    # Everything in the family is dead, including the newest refresh token and access tokens.
    again = await client.post(f"{API}/auth/refresh", json={"refresh_token": third["refresh_token"]})
    assert again.status_code == 401
    assert (await client.get(f"{API}/people/me", headers=bearer(third))).status_code == 401
    # Other logins of the same user are not affected.
    other = (await login(client, org.owner_email)).json()
    assert (await client.get(f"{API}/people/me", headers=bearer(other))).status_code == 200


async def test_refresh_with_garbage_token(client):
    r = await client.post(f"{API}/auth/refresh", json={"refresh_token": "rt_" + "x" * 40})
    assert r.status_code == 401


async def test_expired_refresh_token(org, client, owner_conn):
    tokens = (await login(client, org.owner_email)).json()
    await owner_conn.execute("UPDATE auth_sessions SET expires_at = now() - interval '1 minute'")
    r = await client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert (await client.get(f"{API}/people/me", headers=bearer(tokens))).status_code == 401


async def test_logout_revokes_session_and_is_idempotent(org, client):
    tokens = (await login(client, org.owner_email)).json()
    for _ in range(2):
        r = await client.post(f"{API}/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 204
    assert (await client.get(f"{API}/people/me", headers=bearer(tokens))).status_code == 401
    r = await client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


async def test_logout_all_kills_every_session(org, client):
    a = (await login(client, org.owner_email)).json()
    b = (await login(client, org.owner_email)).json()
    assert (await client.post(f"{API}/auth/logout-all", headers=bearer(a))).status_code == 204
    for t in (a, b):
        assert (await client.get(f"{API}/people/me", headers=bearer(t))).status_code == 401


async def test_list_and_revoke_sessions(org, client):
    a = (await login(client, org.owner_email, device_label="Pixel 8")).json()
    b = (await login(client, org.owner_email, device_label="Laptop")).json()
    listing = (await client.get(f"{API}/auth/sessions", headers=bearer(a))).json()
    labels = {s["device_label"]: s for s in listing}
    assert {"Pixel 8", "Laptop"} <= set(labels)
    assert labels["Pixel 8"]["current"] is True and labels["Laptop"]["current"] is False
    assert "token" not in str(listing) and "hash" not in str(listing)
    # Rotation does not create a second visible session.
    await client.post(f"{API}/auth/refresh", json={"refresh_token": a["refresh_token"]})
    assert len((await client.get(f"{API}/auth/sessions", headers=bearer(b))).json()) == 3  # +signup

    r = await client.delete(f"{API}/auth/sessions/{labels['Laptop']['id']}", headers=bearer(a))
    assert r.status_code == 204
    assert (await client.get(f"{API}/people/me", headers=bearer(b))).status_code == 401
    assert (await client.get(f"{API}/people/me", headers=bearer(a))).status_code == 200


async def test_cannot_revoke_someone_elses_session(team_org, client):
    sid = (await client.get(f"{API}/auth/sessions", headers=team_org.headers["emp1"])).json()[0][
        "id"
    ]
    r = await client.delete(f"{API}/auth/sessions/{sid}", headers=team_org.headers["emp2"])
    assert r.status_code == 404
    assert (
        await client.get(f"{API}/people/me", headers=team_org.headers["emp1"])
    ).status_code == 200


async def test_missing_malformed_and_foreign_tokens(org, client, app):
    assert (await client.get(f"{API}/people/me")).status_code == 401
    assert (
        await client.get(f"{API}/people/me", headers={"Authorization": "Bearer nope"})
    ).status_code == 401
    assert (
        await client.get(f"{API}/people/me", headers={"Authorization": "Basic abc"})
    ).status_code == 401
    import jwt

    forged = jwt.encode(
        {
            "sub": org.users["owner"]["id"],
            "org": org.id,
            "sid": org.id,
            "exp": 4102444800,
            "typ": "access",
        },
        "another-secret-another-secret-xx",
        algorithm="HS256",
    )
    r = await client.get(f"{API}/people/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- password flows


def reset_token(mailbox, email):
    return re.search(r"Token: (\S+)", mailbox.to(email)[-1].body).group(1)


async def test_forgot_is_uniform_and_sends_mail_only_for_real_accounts(org, client, mailbox):
    r1 = await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
    r2 = await client.post(f"{API}/auth/password/forgot", json={"email": "ghost@example.com"})
    assert r1.status_code == r2.status_code == 202
    assert r1.json() == r2.json()
    assert len(mailbox.to(org.owner_email)) == 1 and not mailbox.to("ghost@example.com")


async def test_reset_password_is_single_use_and_revokes_sessions(org, client, mailbox):
    session = (await login(client, org.owner_email)).json()
    await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
    token = reset_token(mailbox, org.owner_email)
    new_pw = "brand-new-password-1"
    r = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": new_pw}
    )
    assert r.status_code == 200
    again = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": "yet-another-pass-2"}
    )
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_token"
    assert (await login(client, org.owner_email, new_pw)).status_code == 200
    assert (await login(client, org.owner_email)).status_code == 401
    assert (await client.get(f"{API}/people/me", headers=bearer(session))).status_code == 401


async def test_reset_token_expires(org, client, mailbox, owner_conn):
    await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
    token = reset_token(mailbox, org.owner_email)
    await owner_conn.execute("UPDATE auth_tokens SET expires_at = now() - interval '1 second'")
    r = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": "brand-new-password-1"}
    )
    assert r.status_code == 400


async def test_new_reset_request_invalidates_the_old_token(org, client, mailbox):
    await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
    old = reset_token(mailbox, org.owner_email)
    await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
    new = reset_token(mailbox, org.owner_email)
    body = {"new_password": "brand-new-password-1"}
    assert (
        await client.post(f"{API}/auth/password/reset", json={"token": old, **body})
    ).status_code == 400
    assert (
        await client.post(f"{API}/auth/password/reset", json={"token": new, **body})
    ).status_code == 200


async def test_reset_rejects_weak_password_without_burning_the_token(org, client, mailbox):
    await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
    token = reset_token(mailbox, org.owner_email)
    r = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": "short"}
    )
    assert r.status_code == 422
    ok = await client.post(
        f"{API}/auth/password/reset", json={"token": token, "new_password": "brand-new-password-1"}
    )
    assert ok.status_code == 200


async def test_forgot_password_is_rate_limited(org, client):
    codes = [
        (
            await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
        ).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [202] * 5 and codes[5:] == [429, 429]


async def test_change_password_keeps_current_session_only(org, client):
    a = (await login(client, org.owner_email)).json()
    b = (await login(client, org.owner_email)).json()
    bad = await client.post(
        f"{API}/auth/password/change",
        json={"current_password": "nope-nope-nope", "new_password": "brand-new-password-1"},
        headers=bearer(a),
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "wrong_password"
    ok = await client.post(
        f"{API}/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "brand-new-password-1"},
        headers=bearer(a),
    )
    assert ok.status_code == 200
    assert (await client.get(f"{API}/people/me", headers=bearer(a))).status_code == 200
    assert (await client.get(f"{API}/people/me", headers=bearer(b))).status_code == 401
    assert (await login(client, org.owner_email, "brand-new-password-1")).status_code == 200


# ------------------------------------------------------------------------------- invites


async def test_invite_flow_and_single_use(org, client, mailbox):
    r = await client.post(
        f"{API}/people",
        json={"name": "Ivy Invitee", "email": "ivy@example.com"},
        headers=org.owner_headers,
    )
    assert r.status_code == 201 and r.json()["status"] == "invited"
    assert (await login(client, "ivy@example.com", PASSWORD)).status_code == 401  # no password yet
    token = invite_token(mailbox, "ivy@example.com")
    accepted = await client.post(
        f"{API}/auth/invite/accept", json={"token": token, "password": PASSWORD}
    )
    assert accepted.status_code == 200
    assert accepted.json()["user"]["status"] == "active"
    assert (
        await client.get(f"{API}/people/me", headers=bearer(accepted.json()))
    ).status_code == 200
    second = await client.post(
        f"{API}/auth/invite/accept", json={"token": token, "password": "another-password-9"}
    )
    assert second.status_code == 400
    assert (await login(client, "ivy@example.com", PASSWORD)).status_code == 200


async def test_resending_an_invite_invalidates_the_previous_one(org, client, mailbox):
    r = await client.post(
        f"{API}/people", json={"name": "Ivy", "email": "ivy@example.com"}, headers=org.owner_headers
    )
    old = invite_token(mailbox, "ivy@example.com")
    uid = r.json()["id"]
    assert (
        await client.post(f"{API}/people/{uid}/invite", headers=org.owner_headers)
    ).status_code == 200
    new = invite_token(mailbox, "ivy@example.com")
    assert old != new
    body = {"password": PASSWORD}
    assert (
        await client.post(f"{API}/auth/invite/accept", json={"token": old, **body})
    ).status_code == 400
    assert (
        await client.post(f"{API}/auth/invite/accept", json={"token": new, **body})
    ).status_code == 200


async def test_expired_invite(org, client, mailbox, owner_conn):
    await client.post(
        f"{API}/people", json={"name": "Ivy", "email": "ivy@example.com"}, headers=org.owner_headers
    )
    token = invite_token(mailbox, "ivy@example.com")
    await owner_conn.execute(
        "UPDATE auth_tokens SET expires_at = now() - interval '1 second' WHERE kind = 'invite'"
    )
    r = await client.post(f"{API}/auth/invite/accept", json={"token": token, "password": PASSWORD})
    assert r.status_code == 400


async def test_reset_token_cannot_be_used_as_invite_and_vice_versa(org, client, mailbox):
    await client.post(f"{API}/auth/password/forgot", json={"email": org.owner_email})
    token = reset_token(mailbox, org.owner_email)
    r = await client.post(f"{API}/auth/invite/accept", json={"token": token, "password": PASSWORD})
    assert r.status_code == 400


# ------------------------------------------------------------------------------- disabled


async def test_disabling_a_user_revokes_sessions_and_blocks_login(team_org, client):
    emp = team_org.users["emp1"]
    tokens = team_org.tokens["emp1"]
    r = await client.post(f"{API}/people/{emp['id']}/disable", headers=team_org.owner_headers)
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert (await client.get(f"{API}/people/me", headers=bearer(tokens))).status_code == 401
    refresh = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401
    blocked = await login(client, emp["email"])
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "account_disabled"
    # Re-enabling lets them back in (password kept).
    assert (
        await client.post(f"{API}/people/{emp['id']}/enable", headers=team_org.owner_headers)
    ).status_code == 200
    assert (await login(client, emp["email"])).status_code == 200


async def test_admin_can_revoke_all_sessions_of_a_user(team_org, client):
    emp = team_org.users["emp2"]
    r = await client.post(
        f"{API}/people/{emp['id']}/revoke-sessions", headers=team_org.headers["admin"]
    )
    assert r.status_code == 200
    assert (
        await client.get(f"{API}/people/me", headers=team_org.headers["emp2"])
    ).status_code == 401
    # Not for a plain employee.
    r = await client.post(
        f"{API}/people/{emp['id']}/revoke-sessions", headers=team_org.headers["emp1"]
    )
    assert r.status_code == 403


async def test_suspended_org_blocks_login_and_api(org, client, owner_conn):
    tokens = (await login(client, org.owner_email)).json()
    await owner_conn.execute("UPDATE organizations SET status = 'suspended'")
    assert (await login(client, org.owner_email)).status_code == 403
    assert (await client.get(f"{API}/people/me", headers=bearer(tokens))).status_code == 403


@pytest.mark.parametrize("pw", ["short", "x" * 129])
async def test_signup_password_policy(client, pw):
    r = await client.post(
        f"{API}/orgs/signup",
        json={"org_name": "Acme", "name": "A", "email": "a@acme.example.com", "password": pw},
    )
    assert r.status_code == 422


async def test_passwords_are_hashed_with_argon2(org, owner_conn):
    h = await owner_conn.fetchval("SELECT password_hash FROM users LIMIT 1")
    assert h.startswith("$argon2")
    assert PASSWORD not in h


async def test_refresh_tokens_stored_only_as_sha256(org, owner_conn, client):
    tokens = (await login(client, org.owner_email)).json()
    hashes = [r[0] for r in await owner_conn.fetch("SELECT token_hash FROM auth_sessions")]
    assert tokens["refresh_token"] not in hashes
    import hashlib

    assert hashlib.sha256(tokens["refresh_token"].encode()).hexdigest() in hashes


async def test_app_role_cannot_read_rate_limit_ledger(cluster):
    import asyncpg

    conn = await asyncpg.connect(cluster.app_url.replace("+asyncpg", ""))
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM auth_attempts")
    finally:
        await conn.close()
