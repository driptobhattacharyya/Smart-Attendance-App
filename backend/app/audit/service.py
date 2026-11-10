"""Append-only audit trail. Every mutating service in the foundation calls ``write_audit``."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.pagination import keyset_page
from .models import AuditLog

_REDACT = ("hash", "secret", "password", "token")


def sanitize(value: Any) -> Any:
    """JSON-safe copy with anything that looks like a credential dropped."""
    if value is None:
        return None
    safe = json.loads(json.dumps(value, default=str))

    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: scrub(v) for k, v in node.items() if not any(w in k.lower() for w in _REDACT)
            }
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return node

    return scrub(safe)


async def write_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    actor_kind: str = "user",
    target_type: str | None = None,
    target_id: Any = None,
    before: Any = None,
    after: Any = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    row = AuditLog(
        org_id=org_id,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        before=sanitize(before),
        after=sanitize(after),
        ip=ip,
        user_agent=(user_agent or "")[:300] or None,
    )
    session.add(row)
    await session.flush()
    return row


async def list_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> tuple[list[AuditLog], str | None]:
    stmt = select(AuditLog)
    if actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if action:
        # "people." matches every people.* action; otherwise an exact match.
        if action.endswith("."):
            stmt = stmt.where(AuditLog.action.startswith(action))
        else:
            stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if since:
        stmt = stmt.where(AuditLog.at >= since)
    if until:
        stmt = stmt.where(AuditLog.at < until)
    return await keyset_page(
        session, stmt, [AuditLog.at, AuditLog.id], limit=limit, cursor=cursor, descending=True
    )
