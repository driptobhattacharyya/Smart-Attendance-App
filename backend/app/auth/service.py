"""Authenticated auth operations (tenant session): sessions list/revoke, password change."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from ..core.errors import AppError, not_found
from ..core.security import utcnow
from ..people.models import User
from .deps import Ctx
from .models import AuthSession
from .schemas import SessionOut
from .sessions import revoke_family, revoke_user_sessions


async def list_sessions(ctx: Ctx, user_id: uuid.UUID | None = None) -> list[SessionOut]:
    target = user_id or ctx.user_id
    rows = (
        await ctx.db.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == target,
                AuthSession.replaced_by.is_(None),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > utcnow(),
            )
            .order_by(AuthSession.last_used_at.desc())
        )
    ).scalars()
    return [
        SessionOut(
            id=r.family_id,
            device_label=r.device_label,
            ip=r.ip,
            user_agent=r.user_agent,
            created_at=r.family_created_at,
            last_used_at=r.last_used_at,
            expires_at=r.expires_at,
            current=r.family_id == ctx.principal.session_id,
        )
        for r in rows
    ]


async def revoke_session(ctx: Ctx, session_id: uuid.UUID) -> None:
    """Own sessions only (admins use people.revoke_user_sessions)."""
    owner = (
        await ctx.db.execute(
            select(AuthSession.user_id).where(AuthSession.family_id == session_id).limit(1)
        )
    ).scalar_one_or_none()
    if owner != ctx.user_id:
        raise not_found("Session")
    await revoke_family(ctx.db, session_id)
    await ctx.audit("auth.session_revoked", "session", session_id)


async def logout_all(ctx: Ctx) -> int:
    count = await revoke_user_sessions(ctx.db, ctx.user_id)
    await ctx.audit("auth.logout_all", "user", ctx.user_id)
    return count


async def change_password(ctx: Ctx, current_password: str, new_password: str) -> None:
    user = (await ctx.db.execute(select(User).where(User.id == ctx.user_id))).scalar_one()
    if not await ctx.hasher.verify(current_password, user.password_hash):
        raise AppError(400, "wrong_password", "Current password is incorrect")
    user.password_hash = await ctx.hasher.hash(new_password)
    # Every other device must sign in again; this one stays.
    await revoke_user_sessions(ctx.db, user.id, except_family=ctx.principal.session_id)
    await ctx.audit("auth.password_changed", "user", user.id)
