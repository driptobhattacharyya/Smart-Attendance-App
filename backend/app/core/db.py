"""Engines and sessions for the two database roles.

* ``attendify_app`` (``Database.tenant_session``): what request handlers use. Every transaction
  starts with ``SET LOCAL app.org_id = '<uuid>'``; row level security does the rest.
* ``attendify_system`` (``Database.system_session``): ``BYPASSRLS``. **Only** the modules listed in
  ``SYSTEM_SESSION_ALLOWED`` may call it (enforced by tests/test_system_session_usage.py):
  org signup, pre-authentication auth flows, desk device-token flows and internal jobs.

Both engines are built for transaction-mode poolers (Supabase, Neon, RDS Proxy): asyncpg's
prepared statement cache is off and tenant context is always ``SET LOCAL`` inside a transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from .config import Settings
from .models import Base  # noqa: F401  (re-export)

# Relative to the ``app`` package. Anything else using ``system_session`` fails the test suite.
SYSTEM_SESSION_ALLOWED = (
    "core/db.py",
    "tenancy/signup.py",
    "auth/preauth.py",
    "places/desk_system.py",
    "jobs/",  # step 2: internal scheduled jobs
)


def make_engine(url: str, settings: Settings) -> AsyncEngine:
    kwargs: dict = {
        "pool_pre_ping": True,
        "connect_args": {
            # Transaction poolers hand a different server connection to every transaction.
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
        },
    }
    if settings.db_null_pool:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
    return create_async_engine(url, **kwargs)


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.app_engine = make_engine(settings.database_url, settings)
        self._app_sessions = async_sessionmaker(
            self.app_engine, expire_on_commit=False, autoflush=False
        )
        self._system_engine: AsyncEngine | None = None
        self._system_sessions: async_sessionmaker[AsyncSession] | None = None

    @asynccontextmanager
    async def tenant_session(self, org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        """One transaction scoped to ``org_id``; commits on success, rolls back on error."""
        if not isinstance(org_id, uuid.UUID):  # the value is interpolated: never a plain str
            raise TypeError("org_id must be a UUID")
        async with self._app_sessions() as session:
            try:
                # SET LOCAL cannot take bind parameters; a UUID's text form is injection-safe.
                await session.execute(text(f"SET LOCAL app.org_id = '{org_id}'"))
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def system_session(self) -> AsyncIterator[AsyncSession]:
        """BYPASSRLS session. See the module docstring for who may use it."""
        if self._system_sessions is None:
            self._system_engine = make_engine(self.settings.database_system_url, self.settings)
            self._system_sessions = async_sessionmaker(
                self._system_engine, expire_on_commit=False, autoflush=False
            )
        async with self._system_sessions() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def ping(self) -> bool:
        async with self.app_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.app_engine.dispose()
        if self._system_engine is not None:
            await self._system_engine.dispose()


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings
