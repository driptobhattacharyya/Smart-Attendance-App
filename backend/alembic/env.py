"""Async Alembic environment. Runs as the table OWNER role (never attendify_app).

URL precedence: ``-x url=...`` / ``config.attributes['url']`` (tests), env ``ALEMBIC_DATABASE_URL``,
then settings ``DATABASE_OWNER_URL``, then ``DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import asyncpg_url, get_settings

config = context.config


def database_url() -> str:
    url = (
        config.attributes.get("url")
        or context.get_x_argument(as_dictionary=True).get("url")
        or os.environ.get("ALEMBIC_DATABASE_URL")
    )
    if not url:
        settings = get_settings()
        url = settings.database_owner_url or settings.database_url
    return asyncpg_url(url)


def run_migrations_offline() -> None:
    context.configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(
        database_url(),
        poolclass=pool.NullPool,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
