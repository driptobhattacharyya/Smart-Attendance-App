"""Authentication flows that run before any tenant is known (system session).

login lookup by e-mail / org slug + employee code, refresh-token lookup and rotation, logout by
refresh token, forgot/reset password, invite accept. Each is a small named function; the rest of
the application uses the tenant session.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import write_audit
from ..core.config import Settings
from ..core.db import Database
from ..core.email import Outbox
from ..core.errors import AppError, forbidden, unauthorized
from ..core.security import PasswordHasher, new_token, sha256_hex, utcnow
from ..people.models import User
from ..people.schemas import UserOut
from ..tenancy.models import Organization
from . import ratelimit
from .emails import queue_reset_email
from .models import AuthSession, AuthToken, Consent
from .schemas import InviteAcceptIn, LoginIn, LoginOut, TokenPair
from .sessions import issue_session, refresh_hash, revoke_family, revoke_user_sessions

RESET_PREFIX = "pw_"
INVITE_PREFIX = "iv_"
INVALID_CREDENTIALS = ("Invalid credentials", "invalid_credentials")


def _invalid_credentials() -> AppError:
    return unauthorized(*INVALID_CREDENTIALS)


async def _consent_required(s: AsyncSession, org: Organization, user_id: uuid.UUID) -> bool:
    found = (
        await s.execute(
            select(Consent.id).where(
                Consent.user_id == user_id,
                Consent.version >= org.consent_version,
                Consent.withdrawn_at.is_(None),
            )
        )
    ).first()
    return found is None


async def _login_out(s: AsyncSession, tokens: TokenPair, user: User, org: Organization) -> LoginOut:
    return LoginOut(
        **tokens.model_dump(),
        user=UserOut.model_validate(user),
        consent_required=await _consent_required(s, org, user.id),
    )


async def login(
    database: Database,
    settings: Settings,
    hasher: PasswordHasher,
    data: LoginIn,
    *,
    ip: str | None,
    user_agent: str | None,
) -> LoginOut:
    if data.email is not None:
        email = data.email.lower()
        ident = f"email:{email}"
    else:
        ident = f"code:{(data.org_slug or '').lower()}:{data.employee_code}"
    keys = [ident] + ([f"ip:{ip}"] if ip else [])
    window = timedelta(minutes=settings.login_window_minutes)

    async with database.system_session() as s:
        await ratelimit.enforce(s, "login", keys, limit=settings.login_max_failures, window=window)
        if data.email is not None:
            stmt = select(User).where(func.lower(User.email) == email)
        else:
            stmt = (
                select(User)
                .join(Organization, Organization.id == User.org_id)
                .where(
                    func.lower(Organization.slug) == (data.org_slug or "").lower(),
                    User.employee_code == data.employee_code,
                )
            )
        user = (await s.execute(stmt)).scalar_one_or_none()
        ok = await hasher.verify(data.password, user.password_hash if user else None)
        if not ok or user is None or user.status == "invited":
            for key in keys:
                await ratelimit.record(s, "login", key)
            await s.commit()  # failures must persist even though we raise
            raise _invalid_credentials()
        if user.status == "disabled":
            raise forbidden("This account has been disabled", "account_disabled")
        org = (
            await s.execute(select(Organization).where(Organization.id == user.org_id))
        ).scalar_one()
        if org.status != "active":
            raise forbidden("This organization is suspended", "org_suspended")

        await ratelimit.clear(s, "login", ident)
        user.last_login_at = utcnow()
        tokens, _ = await issue_session(
            s,
            settings,
            org_id=user.org_id,
            user_id=user.id,
            device_label=data.device_label,
            ip=ip,
            user_agent=user_agent,
        )
        await write_audit(
            s,
            org_id=user.org_id,
            actor_user_id=user.id,
            action="auth.login",
            target_type="user",
            target_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        return await _login_out(s, tokens, user, org)


async def refresh(
    database: Database,
    settings: Settings,
    refresh_token: str,
    *,
    ip: str | None,
    user_agent: str | None,
) -> TokenPair:
    async with database.system_session() as s:
        row = (
            await s.execute(
                select(AuthSession)
                .where(AuthSession.token_hash == refresh_hash(refresh_token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise unauthorized("Invalid refresh token", "invalid_refresh_token")
        if row.replaced_by is not None:
            # An already-rotated token came back: assume theft, kill the whole family.
            await revoke_family(s, row.family_id)
            await write_audit(
                s,
                org_id=row.org_id,
                actor_user_id=row.user_id,
                actor_kind="system",
                action="auth.refresh_reuse_detected",
                target_type="session",
                target_id=row.family_id,
                ip=ip,
                user_agent=user_agent,
            )
            await s.commit()
            raise unauthorized("Refresh token was already used", "token_reuse_detected")
        if row.revoked_at is not None or row.expires_at <= utcnow():
            raise unauthorized("Refresh token expired or revoked", "invalid_refresh_token")

        user = (await s.execute(select(User).where(User.id == row.user_id))).scalar_one()
        org = (
            await s.execute(select(Organization).where(Organization.id == row.org_id))
        ).scalar_one()
        if user.status != "active" or org.status != "active":
            await revoke_family(s, row.family_id)
            await s.commit()
            raise unauthorized("Account is not active", "account_inactive")

        tokens, new_row = await issue_session(
            s,
            settings,
            org_id=row.org_id,
            user_id=row.user_id,
            device_label=row.device_label,
            ip=ip or row.ip,
            user_agent=user_agent or row.user_agent,
            family_id=row.family_id,
            family_created_at=row.family_created_at,
        )
        row.replaced_by = new_row.id
        row.last_used_at = utcnow()
        return tokens


async def logout(database: Database, refresh_token: str, *, ip: str | None) -> None:
    """Idempotent: an unknown or already-revoked token is simply ignored."""
    async with database.system_session() as s:
        row = (
            await s.execute(
                select(AuthSession).where(AuthSession.token_hash == refresh_hash(refresh_token))
            )
        ).scalar_one_or_none()
        if row is None:
            return
        if await revoke_family(s, row.family_id):
            await write_audit(
                s,
                org_id=row.org_id,
                actor_user_id=row.user_id,
                action="auth.logout",
                target_type="session",
                target_id=row.family_id,
                ip=ip,
            )


async def forgot_password(
    database: Database, settings: Settings, email: str, *, ip: str | None, outbox: Outbox
) -> None:
    """Always looks the same from outside; mails a single-use token if the account is active."""
    email = email.lower()
    keys = [f"email:{email}"] + ([f"ip:{ip}"] if ip else [])
    async with database.system_session() as s:
        await ratelimit.enforce(
            s, "forgot", keys, limit=settings.forgot_max_requests, window=timedelta(hours=1)
        )
        for key in keys:
            await ratelimit.record(s, "forgot", key)
        user = (
            await s.execute(select(User).where(func.lower(User.email) == email))
        ).scalar_one_or_none()
        if user is None or user.status != "active":
            return
        await s.execute(
            update(AuthToken)
            .where(
                AuthToken.user_id == user.id, AuthToken.kind == "reset", AuthToken.used_at.is_(None)
            )
            .values(used_at=func.now())
        )
        token = new_token(RESET_PREFIX)
        s.add(
            AuthToken(
                org_id=user.org_id,
                user_id=user.id,
                kind="reset",
                token_hash=sha256_hex(token),
                expires_at=utcnow() + timedelta(minutes=settings.reset_token_minutes),
            )
        )
        await write_audit(
            s,
            org_id=user.org_id,
            actor_user_id=user.id,
            actor_kind="system",
            action="auth.password_reset_requested",
            target_type="user",
            target_id=user.id,
            ip=ip,
        )
        queue_reset_email(outbox, settings, to=user.email, name=user.name, token=token)


async def _consume_token(s: AsyncSession, token: str, kind: str) -> AuthToken:
    """Atomically mark a token used; only one caller can win."""
    row = (
        await s.execute(
            update(AuthToken)
            .where(
                AuthToken.token_hash == sha256_hex(token),
                AuthToken.kind == kind,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(AuthToken)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(400, "invalid_token", "This link is invalid, expired or already used")
    return row


async def reset_password(
    database: Database, hasher: PasswordHasher, token: str, new_password: str, *, ip: str | None
) -> None:
    new_hash = await hasher.hash(new_password)
    async with database.system_session() as s:
        row = await _consume_token(s, token, "reset")
        user = (await s.execute(select(User).where(User.id == row.user_id))).scalar_one()
        if user.status != "active":
            raise AppError(400, "invalid_token", "This link is invalid, expired or already used")
        user.password_hash = new_hash
        await revoke_user_sessions(s, user.id)
        await write_audit(
            s,
            org_id=user.org_id,
            actor_user_id=user.id,
            action="auth.password_reset",
            target_type="user",
            target_id=user.id,
            ip=ip,
        )


async def accept_invite(
    database: Database,
    settings: Settings,
    hasher: PasswordHasher,
    data: InviteAcceptIn,
    *,
    ip: str | None,
    user_agent: str | None,
) -> LoginOut:
    new_hash = await hasher.hash(data.password)
    async with database.system_session() as s:
        row = await _consume_token(s, data.token, "invite")
        user = (await s.execute(select(User).where(User.id == row.user_id))).scalar_one()
        if user.status != "invited":
            raise AppError(400, "invalid_token", "This link is invalid, expired or already used")
        org = (
            await s.execute(select(Organization).where(Organization.id == user.org_id))
        ).scalar_one()
        user.password_hash = new_hash
        user.status = "active"
        user.last_login_at = utcnow()
        tokens, _ = await issue_session(
            s,
            settings,
            org_id=user.org_id,
            user_id=user.id,
            device_label="invite",
            ip=ip,
            user_agent=user_agent,
        )
        await write_audit(
            s,
            org_id=user.org_id,
            actor_user_id=user.id,
            action="auth.invite_accepted",
            target_type="user",
            target_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        return await _login_out(s, tokens, user, org)
