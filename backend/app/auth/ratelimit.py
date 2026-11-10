"""Rate limiting stored in Postgres (``auth_attempts``); no Redis needed.

Used from system sessions only (the ledger is not tenant data and ``attendify_app`` has no grant).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..core.security import utcnow
from .models import AuthAttempt


async def record(session: AsyncSession, kind: str, key: str, success: bool = False) -> None:
    await session.execute(insert(AuthAttempt).values(kind=kind, key=key[:300], success=success))


async def failures(session: AsyncSession, kind: str, key: str, window: timedelta) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(AuthAttempt)
            .where(
                AuthAttempt.kind == kind,
                AuthAttempt.key == key[:300],
                AuthAttempt.success.is_(False),
                AuthAttempt.at > utcnow() - window,
            )
        )
    ).scalar_one()


async def enforce(
    session: AsyncSession, kind: str, keys: list[str], *, limit: int, window: timedelta
) -> None:
    """429 when any of ``keys`` already has ``limit`` failures inside ``window``."""
    for key in keys:
        if await failures(session, kind, key, window) >= limit:
            raise AppError(
                429,
                "rate_limited",
                "Too many attempts. Try again later.",
                headers={"Retry-After": str(int(window.total_seconds()))},
            )


async def clear(session: AsyncSession, kind: str, key: str) -> None:
    await session.execute(
        delete(AuthAttempt).where(AuthAttempt.kind == kind, AuthAttempt.key == key[:300])
    )
