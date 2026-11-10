import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.core import crypto
from app.core.config import Settings
from app.core.errors import AppError
from app.core.idempotency import purge_expired, run_idempotent
from app.core.pagination import decode_cursor, encode_cursor
from app.main import create_app

from .conftest import make_settings

API = "/api/v1"


# --------------------------------------------------------------------------- idempotency


async def test_idempotency_replays_the_first_response(app, org):
    org_id = uuid.UUID(org.id)
    calls = []

    async def handler():
        calls.append(1)
        return 201, {"n": len(calls)}

    for expected_replay in (False, True, True):
        async with app.state.db.tenant_session(org_id) as s:
            status, body, replayed = await run_idempotent(
                s,
                org_id=org_id,
                user_id=None,
                key="abc",
                scope="POST /x",
                payload={"a": 1},
                handler=handler,
            )
        assert (status, body, replayed) == (201, {"n": 1}, expected_replay)
    assert len(calls) == 1


async def test_idempotency_key_reuse_with_other_payload_is_422(app, org):
    org_id = uuid.UUID(org.id)

    async def handler():
        return 200, {}

    async with app.state.db.tenant_session(org_id) as s:
        await run_idempotent(
            s, org_id=org_id, user_id=None, key="k", scope="s", payload={"a": 1}, handler=handler
        )
    with pytest.raises(AppError) as err:
        async with app.state.db.tenant_session(org_id) as s:
            await run_idempotent(
                s,
                org_id=org_id,
                user_id=None,
                key="k",
                scope="s",
                payload={"a": 2},
                handler=handler,
            )
    assert err.value.status_code == 422 and err.value.code == "idempotency_key_reuse"


async def test_idempotency_keys_are_per_org(app, make_org):
    a, b = await make_org("Alpha"), await make_org("Beta")
    runs = []

    async def handler():
        runs.append(1)
        return 200, {"ok": 1}

    for o in (a, b):
        oid = uuid.UUID(o.id)
        async with app.state.db.tenant_session(oid) as s:
            _, _, replayed = await run_idempotent(
                s, org_id=oid, user_id=None, key="same", scope="s", payload={}, handler=handler
            )
            assert replayed is False
    assert len(runs) == 2


async def test_concurrent_requests_with_the_same_key_run_the_handler_once(app, org):
    org_id = uuid.UUID(org.id)
    runs = []

    async def handler():
        runs.append(1)
        await asyncio.sleep(0.2)
        return 200, {"done": True}

    async def attempt():
        async with app.state.db.tenant_session(org_id) as s:
            return await run_idempotent(
                s, org_id=org_id, user_id=None, key="race", scope="s", payload={}, handler=handler
            )

    results = await asyncio.gather(attempt(), attempt(), attempt())
    assert len(runs) == 1
    assert sorted(r[2] for r in results) == [False, True, True]
    assert all(r[1] == {"done": True} for r in results)


async def test_failed_handler_does_not_burn_the_key(app, org):
    org_id = uuid.UUID(org.id)

    async def boom():
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        async with app.state.db.tenant_session(org_id) as s:
            await run_idempotent(
                s, org_id=org_id, user_id=None, key="retry", scope="s", payload={}, handler=boom
            )

    async def ok():
        return 200, {"ok": True}

    async with app.state.db.tenant_session(org_id) as s:
        _, _, replayed = await run_idempotent(
            s, org_id=org_id, user_id=None, key="retry", scope="s", payload={}, handler=ok
        )
    assert replayed is False


async def test_purge_expired(app, org, owner_conn):
    org_id = uuid.UUID(org.id)

    async def handler():
        return 200, {}

    async with app.state.db.tenant_session(org_id) as s:
        await run_idempotent(
            s, org_id=org_id, user_id=None, key="old", scope="s", payload={}, handler=handler
        )
    await owner_conn.execute("UPDATE idempotency_keys SET created_at = now() - interval '3 days'")
    async with app.state.db.tenant_session(org_id) as s:
        assert await purge_expired(s) == 1


# ------------------------------------------------------------------------------ errors


async def test_error_format_for_404_405_422_and_unhandled(client, app):
    r = await client.get(f"{API}/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert set(r.json()["error"]) == {"code", "message", "details"}
    r = await client.delete(f"{API}/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"
    r = await client.post(f"{API}/auth/login", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["details"]["errors"][0]["loc"][0] == "body"

    @app.get("/api/v1/_boom")
    async def boom():
        raise RuntimeError("secret internals")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get(f"{API}/_boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "secret internals" not in r.text


async def test_health_reports_database_down(cluster):
    app = create_app(
        make_settings(cluster, database_url=cluster.app_url.replace("/attendify", "/missing_db"))
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"{API}/health")
    await app.state.db.dispose()
    assert r.status_code == 503 and r.json()["database"] == "down"


# --------------------------------------------------------------------------- unit pieces


def test_cursor_roundtrip_and_tamper_detection():
    values = ["abc", uuid.uuid4(), datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), 7]
    assert decode_cursor(encode_cursor(values), 4) == values
    with pytest.raises(AppError):
        decode_cursor(encode_cursor(values), 3)
    with pytest.raises(AppError):
        decode_cursor("%%%", 1)


def test_crypto_roundtrip_binds_to_aad_and_key():
    key = os.urandom(32)
    blob = crypto.encrypt(key, b"secret", b"row-1")
    assert b"secret" not in blob
    assert crypto.decrypt(key, blob, b"row-1") == b"secret"
    assert crypto.encrypt(key, b"secret", b"row-1") != blob  # fresh nonce
    for bad_key, aad in ((os.urandom(32), b"row-1"), (key, b"row-2")):
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(bad_key, blob, aad)


def test_settings_validation_and_production_guards():
    with pytest.raises(ValueError):
        Settings(_env_file=None, data_key=base64.b64encode(b"short").decode())
    assert Settings(_env_file=None, database_url="postgresql://u@h/db").database_url.startswith(
        "postgresql+asyncpg://"
    )
    prod = Settings(_env_file=None, environment="production")
    with pytest.raises(RuntimeError):
        prod.assert_production_ready()
    ok = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="x" * 40,
        data_key=base64.b64encode(os.urandom(32)).decode(),
    )
    ok.assert_production_ready()


async def test_origin_secret_middleware(cluster, monkeypatch):
    monkeypatch.setenv("ORIGIN_SECRET", "s3cret-value")
    app = create_app(make_settings(cluster))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get(f"{API}/health")).status_code == 200  # exempt
        assert (await c.get(f"{API}/people/me")).status_code == 403
        assert (
            await c.get(f"{API}/people/me", headers={"X-Origin-Auth": "wrong"})
        ).status_code == 403
        assert (
            await c.get(f"{API}/people/me", headers={"X-Origin-Auth": "s3cret-value"})
        ).status_code == 401
    await app.state.db.dispose()


async def test_app_session_requires_uuid_org(app):
    with pytest.raises(TypeError):
        async with app.state.db.tenant_session("x'; DROP TABLE users; --"):
            pass


async def test_migrations_are_reversible(cluster):
    from alembic.config import Config

    from alembic import command

    from .conftest import BACKEND

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["url"] = cluster.owner_url
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
