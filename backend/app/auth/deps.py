"""The authenticated request context: one tenant transaction + the principal behind it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import write_audit
from ..core.config import Settings
from ..core.db import Database
from ..core.email import Outbox
from ..core.errors import forbidden, unauthorized
from ..core.security import PasswordHasher, decode_access_token
from ..people.models import User
from ..tenancy.models import Organization, Role
from .models import AuthSession, Consent

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role_key: str
    permissions: frozenset[str]
    session_id: uuid.UUID
    name: str
    email: str


@dataclass
class Ctx:
    """What services receive: the open tenant session (``SET LOCAL app.org_id`` already run),
    the caller, and request metadata for the audit trail."""

    db: AsyncSession
    principal: Principal
    org: Organization
    settings: Settings
    hasher: PasswordHasher
    ip: str | None = None
    user_agent: str | None = None
    outbox: Outbox = field(default_factory=Outbox)
    # ``database`` lets a service write something that must survive the request's rollback (an
    # audit row for a rejected attempt); ``state`` is ``app.state`` (face provider, integrity
    # verifier). Both are None for contexts built by scripts.
    database: Database | None = None
    state: Any = None

    @property
    def org_id(self) -> uuid.UUID:
        return self.principal.org_id

    @property
    def user_id(self) -> uuid.UUID:
        return self.principal.user_id

    def has(self, permission: str) -> bool:
        return permission in self.principal.permissions

    def require(self, permission: str) -> None:
        if not self.has(permission):
            raise forbidden("You do not have permission to do this", "forbidden")

    async def audit(
        self,
        action: str,
        target_type: str | None = None,
        target_id: Any = None,
        before: Any = None,
        after: Any = None,
    ) -> None:
        await write_audit(
            self.db,
            org_id=self.org_id,
            actor_user_id=self.user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            ip=self.ip,
            user_agent=self.user_agent,
        )


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def get_ctx(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AsyncIterator[Ctx]:
    """Authenticate, open the tenant transaction, load the principal, hand over a ``Ctx``.

    Declared with ``scope="function"`` by ``current_ctx``: the transaction commits (or rolls
    back) before the response is sent, so a failed commit is a 500, never a lost write.
    Permissions are read from the database on every request; nothing is cached.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    settings: Settings = request.app.state.settings
    database: Database = request.app.state.db
    claims = decode_access_token(settings, credentials.credentials)

    async with database.tenant_session(claims.org_id) as session:
        row = (
            await session.execute(
                select(User, Role.permissions, Organization)
                .join(Role, (Role.org_id == User.org_id) & (Role.key == User.role_key))
                .join(Organization, Organization.id == User.org_id)
                .where(User.id == claims.user_id)
            )
        ).first()
        if row is None:
            raise unauthorized("Invalid or expired token", "invalid_token")
        user, permissions, org = row
        if user.status != "active":
            raise unauthorized("Account is not active", "account_inactive")
        if org.status != "active":
            raise forbidden("This organization is suspended", "org_suspended")
        live = (
            await session.execute(
                select(AuthSession.id).where(
                    AuthSession.family_id == claims.session_id,
                    AuthSession.user_id == user.id,
                    AuthSession.replaced_by.is_(None),
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > func.now(),
                )
            )
        ).first()
        if live is None:
            raise unauthorized("Session ended", "session_revoked")

        ctx = Ctx(
            db=session,
            principal=Principal(
                user_id=user.id,
                org_id=user.org_id,
                role_key=user.role_key,
                permissions=frozenset(permissions),
                session_id=claims.session_id,
                name=user.name,
                email=user.email,
            ),
            org=org,
            settings=settings,
            hasher=request.app.state.hasher,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            database=database,
            state=request.app.state,
        )
        yield ctx
    # The transaction has committed here. Only now do queued emails go out.
    await ctx.outbox.flush(request.app.state.email)


CurrentCtx = Annotated[Ctx, Depends(get_ctx, scope="function")]


async def consent_required_for(
    session: AsyncSession, org: Organization, user_id: uuid.UUID
) -> bool:
    found = (
        await session.execute(
            select(Consent.id).where(
                Consent.user_id == user_id,
                Consent.version >= org.consent_version,
                Consent.withdrawn_at.is_(None),
            )
        )
    ).first()
    return found is None


async def require_consent(ctx: CurrentCtx) -> Ctx:
    """Attendance and face endpoints (steps 2 and 3) depend on this."""
    if await consent_required_for(ctx.db, ctx.org, ctx.user_id):
        raise forbidden(
            "You must accept the current consent text first",
            "consent_required",
            version=ctx.org.consent_version,
        )
    return ctx
