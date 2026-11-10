from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from ...attendance import corrections, policies, service
from ...attendance.schemas import (
    CheckInIn,
    CheckInOut,
    CheckOutIn,
    CheckOutOut,
    EventOut,
    HeartbeatIn,
    HeartbeatOut,
    IntervalCorrectionIn,
    MeOut,
    PolicyCreate,
    PolicyOut,
    StatusOut,
    VoidEventIn,
)
from ...attendance.trust import NONCE_TTL_SECONDS, issue_nonce
from ...auth.deps import Ctx, require_consent
from ...core.security import utcnow
from ...rbac.deps import require_permission
from ...rbac.permissions import ATTENDANCE_CORRECT, ATTENDANCE_SELF, POLICIES_MANAGE

router = APIRouter(prefix="/attendance", tags=["attendance"])


async def _attendee(ctx: Annotated[Ctx, Depends(require_consent)]) -> Ctx:
    ctx.require(ATTENDANCE_SELF)
    return ctx


Attendee = Annotated[Ctx, Depends(_attendee)]
Idem = Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=200)]


@router.get("/status", response_model=StatusOut)
async def get_status(ctx: Attendee):
    return await service.status(ctx)


@router.get("/integrity-nonce")
async def integrity_nonce(ctx: Attendee):
    """Single-use nonce for a Play Integrity token request (bound to the caller, 5 minutes)."""
    return {
        "nonce": issue_nonce(ctx.settings, ctx.user_id, utcnow()),
        "expires_in": NONCE_TTL_SECONDS,
    }


@router.post("/check-in", response_model=CheckInOut, status_code=201)
async def check_in(data: CheckInIn, ctx: Attendee, idempotency_key: Idem = None):
    status, body = await service.check_in(ctx, data, idempotency_key)
    return JSONResponse(body, status_code=status)


@router.post("/heartbeat", response_model=HeartbeatOut)
async def heartbeat(data: HeartbeatIn, ctx: Attendee, idempotency_key: Idem = None):
    status, body = await service.heartbeat(ctx, data, idempotency_key)
    return JSONResponse(body, status_code=status)


@router.post("/check-out", response_model=CheckOutOut)
async def check_out(ctx: Attendee, data: CheckOutIn | None = None, idempotency_key: Idem = None):
    status, body = await service.check_out(ctx, data or CheckOutIn(), idempotency_key)
    return JSONResponse(body, status_code=status)


@router.get("/me", response_model=MeOut)
async def my_attendance(
    ctx: Attendee,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
):
    return await service.my_days(ctx, start, end)


# ---------------------------------------------------------------------------- policies


@router.get("/policy", response_model=PolicyOut)
async def effective_policy(ctx: Attendee):
    return await policies.effective_policy(ctx)


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(ctx: Annotated[Ctx, Depends(require_permission(POLICIES_MANAGE))]):
    return await policies.list_policies(ctx)


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy(
    data: PolicyCreate, ctx: Annotated[Ctx, Depends(require_permission(POLICIES_MANAGE))]
):
    return await policies.create_policy(ctx, data)


# -------------------------------------------------------------------------- corrections

Corrector = Annotated[Ctx, Depends(require_permission(ATTENDANCE_CORRECT))]


@router.post("/corrections/intervals", response_model=EventOut, status_code=201)
async def add_interval(data: IntervalCorrectionIn, ctx: Corrector):
    return await corrections.add_interval(ctx, data)


@router.post("/corrections/void", response_model=EventOut, status_code=201)
async def void_event(data: VoidEventIn, ctx: Corrector):
    return await corrections.void_event(ctx, data)
