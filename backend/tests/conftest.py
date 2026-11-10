"""Test harness: a throwaway PostgreSQL cluster (or TEST_DATABASE_URL), Alembic once per session,
tables truncated between tests. No database mocks anywhere."""

from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio
from alembic.config import Config

from alembic import command
from app.core.config import Settings
from app.core.email import CapturingEmailSender
from app.face import FaceConfig
from app.face.providers import OpenCVProvider
from app.main import create_app

from .face.fakes import FakeEngine, FakeSpoof

BACKEND = Path(__file__).resolve().parent.parent
APP_PASSWORD = "app-test-pw"
SYSTEM_PASSWORD = "system-test-pw"
PASSWORD = "correct-horse-battery"


def _find_pg_bin() -> Path | None:
    candidates = [os.environ.get("PG_BIN", ""), "/usr/lib/postgresql/16/bin"]
    candidates += sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True)
    for c in candidates:
        if c and (Path(c) / "initdb").exists() and (Path(c) / "pg_ctl").exists():
            return Path(c)
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _with_credentials(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    netloc = f"{user}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class Cluster:
    def __init__(self, owner_url: str, stop=None) -> None:
        self.owner_url = owner_url
        self.app_url = _with_credentials(owner_url, "attendify_app", APP_PASSWORD)
        self.system_url = _with_credentials(owner_url, "attendify_system", SYSTEM_PASSWORD)
        self.stop = stop


def _start_local_cluster(bin_dir: Path) -> Cluster:
    root = Path(tempfile.mkdtemp(prefix="attendify-pg-"))
    as_root = os.geteuid() == 0
    prefix: list[str] = []
    if as_root:  # postgres refuses to run as root
        shutil.chown(root, "postgres", "postgres")
        prefix = ["runuser", "-u", "postgres", "--"]
    data = root / "data"
    port = _free_port()

    def run(*args: str) -> None:
        subprocess.run([*prefix, *args], check=True, capture_output=True, text=True, cwd=root)

    run(
        str(bin_dir / "initdb"),
        "-D",
        str(data),
        "-U",
        "postgres",
        "--auth=trust",
        "-E",
        "UTF8",
        "--no-sync",
    )
    run(
        str(bin_dir / "pg_ctl"),
        "-D",
        str(data),
        "-w",
        "-l",
        str(root / "pg.log"),
        "-o",
        f"-p {port} -k {root} -c listen_addresses=127.0.0.1 -c fsync=off "
        "-c synchronous_commit=off -c full_page_writes=off",
        "start",
    )
    run(
        str(bin_dir / "createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "attendify"
    )

    def stop() -> None:
        subprocess.run(
            [*prefix, str(bin_dir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
            capture_output=True,
            cwd=root,
        )
        shutil.rmtree(root, ignore_errors=True)

    return Cluster(f"postgresql+asyncpg://postgres@127.0.0.1:{port}/attendify", stop)


async def _reset_schema(url: str) -> None:
    conn = await asyncpg.connect(url.replace("+asyncpg", ""))
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def cluster() -> Cluster:
    env_url = os.environ.get("TEST_DATABASE_URL")
    if env_url:
        c = Cluster(env_url.replace("postgresql://", "postgresql+asyncpg://", 1))
        asyncio.run(_reset_schema(c.owner_url))
    else:
        bin_dir = _find_pg_bin()
        if bin_dir is None:
            pytest.skip(
                "PostgreSQL binaries not found (looked in /usr/lib/postgresql/*/bin); install "
                "postgresql-16 or set TEST_DATABASE_URL to a postgres:16 superuser URL"
            )
        c = _start_local_cluster(bin_dir)

    os.environ["ATTENDIFY_APP_PASSWORD"] = APP_PASSWORD
    os.environ["ATTENDIFY_SYSTEM_PASSWORD"] = SYSTEM_PASSWORD
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["url"] = c.owner_url
    command.upgrade(cfg, "head")
    yield c
    if c.stop:
        c.stop()


@pytest_asyncio.fixture
async def owner_conn(cluster: Cluster) -> AsyncIterator[asyncpg.Connection]:
    """Superuser connection (bypasses RLS) for arranging and inspecting data."""
    conn = await asyncpg.connect(cluster.owner_url.replace("+asyncpg", ""))
    yield conn
    await conn.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(cluster: Cluster) -> AsyncIterator[None]:
    conn = await asyncpg.connect(cluster.owner_url.replace("+asyncpg", ""))
    try:
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
        )
        names = ", ".join(f'"{t["tablename"]}"' for t in tables)
        await conn.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
    yield


def make_settings(cluster: Cluster, **overrides) -> Settings:
    values = dict(
        environment="test",
        database_url=cluster.app_url,
        database_system_url=cluster.system_url,
        db_null_pool=True,
        email_backend="capture",
        argon2_time_cost=1,
        argon2_memory_cost=64,
        jwt_secret="test-secret-test-secret-test-secret",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(cluster: Cluster) -> Settings:
    return make_settings(cluster)


@pytest_asyncio.fixture
async def app(settings: Settings):
    application = create_app(settings)
    engine = FakeEngine()
    application.state.face_provider = OpenCVProvider(engine, FakeSpoof(engine), FaceConfig())
    yield application
    await application.state.db.dispose()


@pytest.fixture
def mailbox(app) -> CapturingEmailSender:
    return app.state.email


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class Org:
    """A signed-up organization plus helpers to add people through the real API."""

    def __init__(self, client, mailbox, signup: dict, password: str) -> None:
        self.client = client
        self.mailbox = mailbox
        self.signup = signup
        self.password = password
        self.owner_email = signup["user"]["email"]
        self.id = signup["org"]["id"]
        self.slug = signup["org"]["slug"]
        self.owner_headers = bearer(signup)
        self.users: dict[str, dict] = {"owner": signup["user"]}
        self.headers: dict[str, dict[str, str]] = {"owner": self.owner_headers}
        self.tokens: dict[str, dict] = {"owner": signup}

    async def add_user(
        self, key: str, *, role: str = "employee", manager: str | None = None, **extra
    ) -> dict:
        """Invite through the API and accept the invite (so the user is active and logged in)."""
        email = f"{key}.{self.slug}@example.com"
        body = {"name": key.title(), "email": email, "role_key": role, **extra}
        if manager:
            body["manager_id"] = self.users[manager]["id"]
        resp = await self.client.post("/api/v1/people", json=body, headers=self.owner_headers)
        assert resp.status_code == 201, resp.text
        user = resp.json()
        token = invite_token(self.mailbox, email)
        accepted = await self.client.post(
            "/api/v1/auth/invite/accept", json={"token": token, "password": self.password}
        )
        assert accepted.status_code == 200, accepted.text
        self.users[key] = user
        self.tokens[key] = accepted.json()
        self.headers[key] = bearer(accepted.json())
        return user


def invite_token(mailbox: CapturingEmailSender, email: str) -> str:
    message = mailbox.to(email)[-1]
    return re.search(r"Token: (\S+)", message.body).group(1)


@pytest_asyncio.fixture
async def make_org(client, mailbox):
    counter = 0

    async def _make(name: str | None = None, password: str = PASSWORD) -> Org:
        nonlocal counter
        counter += 1
        name = name or f"Acme {counter}"
        resp = await client.post(
            "/api/v1/orgs/signup",
            json={
                "org_name": name,
                "name": "Olivia Owner",
                "email": f"owner{counter}@{name.lower().replace(' ', '')}.example.com",
                "password": password,
            },
        )
        assert resp.status_code == 201, resp.text
        return Org(client, mailbox, resp.json(), password)

    return _make


@pytest_asyncio.fixture
async def org(make_org) -> Org:
    return await make_org()


@pytest_asyncio.fixture
async def team_org(make_org) -> Org:
    """owner > admin; owner > boss(manager) > lead(manager) > emp1, emp2; plus outsider employee."""
    o = await make_org("Team Co")
    await o.add_user("admin", role="admin")
    await o.add_user("boss", role="manager")
    await o.add_user("lead", role="manager", manager="boss")
    await o.add_user("emp1", manager="lead")
    await o.add_user("emp2", manager="lead")
    await o.add_user("direct", manager="boss")
    await o.add_user("outsider")
    return o
