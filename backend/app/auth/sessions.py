"""Refresh-token sessions: issue, and revoke by family / user. Works on any open session."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import Settings
from ..core.security import AccessClaims, create_access_token, new_token, sha256_hex, utcnow
from .models import AuthSession
from .schemas import TokenPair

REFRESH_PREFIX = "rt_"


def refresh_hash(token: str) -> str:
    return sha256_hex(token)


async def issue_session(
    session: AsyncSession,
    settings: Settings,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    device_label: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    family_id: uuid.UUID | None = None,
    family_created_at=None,
) -> tuple[TokenPair, AuthSession]:
    """New refresh token (new family unless ``family_id`` is given, i.e. a rotation)."""
    now = utcnow()
    token = new_token(REFRESH_PREFIX)
    row = AuthSession(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=refresh_hash(token),
        device_label=(device_label or "")[:120] or None,
        ip=ip,
        user_agent=(user_agent or "")[:300] or None,
        created_at=now,
        family_created_at=family_created_at or now,
        last_used_at=now,
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )
    session.add(row)
    await session.flush()
    access, expires_in = create_access_token(
        settings, AccessClaims(user_id=user_id, org_id=org_id, session_id=row.family_id)
    )
    return (
        TokenPair(access_token=access, refresh_token=token, expires_in=expires_in),
        row,
    )


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> int:
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    return result.rowcount or 0


async def revoke_user_sessions(
    session: AsyncSession, user_id: uuid.UUID, *, except_family: uuid.UUID | None = None
) -> int:
    stmt = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if except_family is not None:
        stmt = stmt.where(AuthSession.family_id != except_family)
    result = await session.execute(stmt.values(revoked_at=func.now()))
    return result.rowcount or 0
