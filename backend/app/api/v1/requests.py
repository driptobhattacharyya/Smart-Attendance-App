from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from ...attendance import requests_service as svc
from ...attendance.schemas import DecisionIn, RequestCreate, RequestOut
from ...auth.deps import Ctx, CurrentCtx
from ...core.pagination import Page
from ...rbac.deps import require_any_permission, require_permission
from ...rbac.permissions import ATTENDANCE_SELF, REQUESTS_APPROVE_ORG, REQUESTS_APPROVE_TEAM

router = APIRouter(prefix="/requests", tags=["requests"])

Self = Annotated[Ctx, Depends(require_permission(ATTENDANCE_SELF))]
Approver = Annotated[
    Ctx, Depends(require_any_permission(REQUESTS_APPROVE_TEAM, REQUESTS_APPROVE_ORG))
]
Status = Literal["pending", "approved", "rejected", "cancelled"]


@router.post("", response_model=RequestOut, status_code=201)
async def create_request(data: RequestCreate, ctx: Self):
    return await svc.create_request(ctx, data)


@router.get("/me", response_model=Page[RequestOut])
async def my_requests(
    ctx: Self,
    status: Status | None = None,
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = None,
):
    items, nxt = await svc.list_mine(ctx, status, limit, cursor)
    return Page[RequestOut](items=items, next_cursor=nxt)


@router.get("", response_model=Page[RequestOut])
async def approval_queue(
    ctx: Approver,
    status: Status | None = None,
    kind: Literal["correction", "leave", "wfh"] | None = None,
    user_id: uuid.UUID | None = None,
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = None,
):
    items, nxt = await svc.list_for_approver(
        ctx, status=status, kind=kind, user_id=user_id, limit=limit, cursor=cursor
    )
    return Page[RequestOut](items=items, next_cursor=nxt)


@router.post("/{request_id}/decision", response_model=RequestOut)
async def decide(request_id: uuid.UUID, data: DecisionIn, ctx: Approver):
    return await svc.decide(ctx, request_id, data)


@router.post("/{request_id}/cancel", response_model=RequestOut)
async def cancel(request_id: uuid.UUID, ctx: CurrentCtx):
    return await svc.cancel(ctx, request_id)
