from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from ...audit.service import list_audit
from ...auth.deps import Ctx
from ...core.pagination import Page
from ...rbac.deps import require_permission
from ...rbac.permissions import AUDIT_VIEW

router = APIRouter(tags=["audit"])


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_kind: str
    action: str
    target_type: str | None
    target_id: str | None
    before: Any | None
    after: Any | None
    ip: str | None
    at: datetime


@router.get("/audit", response_model=Page[AuditOut])
async def get_audit(
    ctx: Annotated[Ctx, Depends(require_permission(AUDIT_VIEW))],
    actor_user_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=100),
    target_type: str | None = Query(default=None, max_length=50),
    target_id: str | None = Query(default=None, max_length=100),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = None,
):
    rows, next_cursor = await list_audit(
        ctx.db,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )
    return Page[AuditOut](items=[AuditOut.model_validate(r) for r in rows], next_cursor=next_cursor)
