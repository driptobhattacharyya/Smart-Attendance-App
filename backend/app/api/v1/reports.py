from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from ...attendance import reports
from ...auth.deps import Ctx, CurrentCtx
from ...rbac.deps import require_any_permission, require_permission
from ...rbac.permissions import ATTENDANCE_VIEW_ORG, ATTENDANCE_VIEW_TEAM, REPORTS_EXPORT

router = APIRouter(prefix="/reports", tags=["reports"])

Viewer = Annotated[Ctx, Depends(require_any_permission(ATTENDANCE_VIEW_TEAM, ATTENDANCE_VIEW_ORG))]
Exporter = Annotated[Ctx, Depends(require_permission(REPORTS_EXPORT))]


@router.get("/presence")
async def presence(
    ctx: Viewer,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    status: str | None = Query(
        default=None,
        pattern="^(present|away|outside|stale|not_checked_in|on_leave|remote)$",
    ),
):
    return await reports.presence_board(
        ctx, office_id=office_id, department_id=department_id, status=status
    )


@router.get("/dashboard")
async def dashboard(ctx: Viewer, date_: date | None = Query(default=None, alias="date")):
    return await reports.dashboard(ctx, date_)


@router.get("/timesheet")
async def timesheet(
    ctx: Viewer,
    start: date,
    end: date,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = None,
):
    return await reports.timesheet(
        ctx,
        start=start,
        end=end,
        office_id=office_id,
        department_id=department_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/export.csv")
async def export_csv(
    ctx: Exporter,
    start: date,
    end: date,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
):
    body = await reports.export_csv(
        ctx, start=start, end=end, office_id=office_id, department_id=department_id
    )
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="attendance_{start}_{end}.csv"'},
    )


@router.get("/people/{user_id}/days/{day}")
async def day_detail(user_id: uuid.UUID, day: date, ctx: CurrentCtx):
    """Own day, or anyone in the caller's scope (404 otherwise)."""
    return await reports.day_detail(ctx, user_id, day)


@router.get("/sessions/{session_id}")
async def session_detail(session_id: uuid.UUID, ctx: Viewer):
    return await reports.session_detail(ctx, session_id)
