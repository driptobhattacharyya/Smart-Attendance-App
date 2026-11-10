"""Corrections, leave and work-from-home requests with team / org approval."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select

from ..auth.deps import Ctx
from ..core.errors import bad_request, conflict, forbidden, not_found
from ..core.pagination import keyset_page
from ..core.security import utcnow
from ..people.models import User
from ..places.models import Office
from ..rbac.permissions import REQUESTS_APPROVE_ORG, REQUESTS_APPROVE_TEAM
from ..rbac.scope import team_user_ids
from .models import Request
from .schemas import DecisionIn, RequestCreate, RequestOut
from .store import append_event, get_user, lock_user, office_info, recompute_days

MODULE_FOR_KIND = {"correction": "regularization", "leave": "leave", "wfh": "wfh"}
MAX_RANGE_DAYS = 92
MAX_CORRECTION_AGE_DAYS = 60
MAX_LEAVE_BACKDATE_DAYS = 31


def snapshot(r: Request) -> dict:
    return {
        "user_id": r.user_id,
        "kind": r.kind,
        "start_date": r.start_date,
        "end_date": r.end_date,
        "entry_time": r.entry_time,
        "exit_time": r.exit_time,
        "status": r.status,
    }


async def _office_tz(ctx: Ctx, user: User) -> tuple[Office | None, str]:
    office = None
    if user.office_id:
        office = (
            await ctx.db.execute(select(Office).where(Office.id == user.office_id))
        ).scalar_one_or_none()
    return office, office_info(office).timezone


def to_out(r: Request, name: str | None = None) -> RequestOut:
    out = RequestOut.model_validate(r)
    out.user_name = name
    return out


async def create_request(ctx: Ctx, data: RequestCreate) -> RequestOut:
    module = MODULE_FOR_KIND[data.kind]
    if not ctx.org.modules.get(module):
        raise forbidden(f"The '{module}' module is not enabled", "module_disabled", module=module)
    user = await get_user(ctx.db, ctx.user_id)
    _, tz = await _office_tz(ctx, user)
    today = utcnow().astimezone(ZoneInfo(tz)).date()
    start, end = data.start_date, data.end_date
    assert end is not None
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise bad_request("range_too_large", f"At most {MAX_RANGE_DAYS} days per request")
    if data.kind == "correction":
        assert data.entry_time and data.exit_time
        if data.exit_time <= data.entry_time:
            raise bad_request("invalid_times", "exit_time must be after entry_time")
        if start > today:
            raise bad_request("future_correction", "You cannot correct a day that has not happened")
        if (today - start).days > MAX_CORRECTION_AGE_DAYS:
            raise bad_request(
                "too_old", f"Corrections can go back {MAX_CORRECTION_AGE_DAYS} days at most"
            )
    elif data.kind == "leave" and (today - start).days > MAX_LEAVE_BACKDATE_DAYS:
        raise bad_request(
            "too_old", f"Leave can be back-dated {MAX_LEAVE_BACKDATE_DAYS} days at most"
        )
    elif data.kind == "wfh" and start < today:
        raise bad_request("past_wfh", "Work from home must be requested for today or later")

    # No two live requests of one kind may overlap (corrections: same day).
    clash = (
        await ctx.db.execute(
            select(Request.id)
            .where(
                Request.user_id == ctx.user_id,
                Request.kind == data.kind,
                Request.status.in_(("pending", "approved")),
                Request.start_date <= end,
                Request.end_date >= start,
            )
            .limit(1)
        )
    ).first()
    if clash:
        raise conflict("request_overlap", "You already have a request for these dates")

    row = Request(
        id=uuid.uuid4(),
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        kind=data.kind,
        start_date=start,
        end_date=end,
        entry_time=data.entry_time,
        exit_time=data.exit_time,
        reason=data.reason,
        status="pending",
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.db.refresh(row)
    await ctx.audit("request.create", "request", row.id, after=snapshot(row))
    return to_out(row, ctx.principal.name)


def _scope_filter(ctx: Ctx):
    """Which requests an approver may see: everything (org) or their reporting tree (team)."""
    if ctx.has(REQUESTS_APPROVE_ORG):
        return None
    if ctx.has(REQUESTS_APPROVE_TEAM):
        return Request.user_id.in_(team_user_ids(ctx.user_id))
    raise forbidden("You do not have permission to do this")


async def list_mine(ctx: Ctx, status: str | None, limit: int | None, cursor: str | None):
    stmt = select(Request).where(Request.user_id == ctx.user_id)
    if status:
        stmt = stmt.where(Request.status == status)
    rows, nxt = await keyset_page(
        ctx.db, stmt, [Request.created_at, Request.id], limit=limit, cursor=cursor, descending=True
    )
    return [to_out(r, ctx.principal.name) for r in rows], nxt


async def list_for_approver(
    ctx: Ctx,
    *,
    status: str | None,
    kind: str | None,
    user_id: uuid.UUID | None,
    limit: int | None,
    cursor: str | None,
):
    scope = _scope_filter(ctx)
    stmt = select(Request)
    if scope is not None:
        stmt = stmt.where(scope)
    for col, value in ((Request.status, status), (Request.kind, kind), (Request.user_id, user_id)):
        if value is not None:
            stmt = stmt.where(col == value)
    rows, nxt = await keyset_page(
        ctx.db, stmt, [Request.created_at, Request.id], limit=limit, cursor=cursor, descending=True
    )
    names = dict(
        (
            await ctx.db.execute(
                select(User.id, User.name).where(User.id.in_({r.user_id for r in rows}))
            )
        ).all()
        if rows
        else []
    )
    return [to_out(r, names.get(r.user_id)) for r in rows], nxt


async def _load_for_decision(ctx: Ctx, request_id: uuid.UUID) -> Request:
    row = (
        await ctx.db.execute(select(Request).where(Request.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Request")
    if row.user_id == ctx.user_id:
        raise forbidden("You cannot decide on your own request", "cannot_approve_own")
    scope = _scope_filter(ctx)
    if scope is not None:
        visible = (
            await ctx.db.execute(select(Request.id).where(Request.id == request_id, scope))
        ).first()
        if visible is None:
            raise not_found("Request")
    return row


def _instant(day: date, at, tz: str) -> datetime:
    return datetime.combine(day, at, tzinfo=ZoneInfo(tz)).astimezone(UTC)


async def decide(ctx: Ctx, request_id: uuid.UUID, data: DecisionIn) -> RequestOut:
    row = await _load_for_decision(ctx, request_id)
    await lock_user(ctx.db, ctx.org_id, row.user_id)
    # Re-read under the lock: of two simultaneous decisions exactly one sees "pending".
    row = (
        await ctx.db.execute(select(Request).where(Request.id == request_id).with_for_update())
    ).scalar_one()
    if row.status != "pending":
        raise conflict("request_not_pending", f"Request is already {row.status}")
    before = snapshot(row)
    row.status = "approved" if data.action == "approve" else "rejected"
    row.decided_by = ctx.user_id
    row.decided_at = utcnow()
    row.comment = data.comment
    await ctx.db.flush()

    target = await get_user(ctx.db, row.user_id)
    office, tz = await _office_tz(ctx, target)
    if row.status == "approved":
        if row.kind == "correction":
            assert row.entry_time and row.exit_time
            await append_event(
                ctx.db,
                org_id=ctx.org_id,
                user_id=row.user_id,
                type="interval_added",
                occurred_at=_instant(row.start_date, row.entry_time, tz),
                source="request",
                day=row.start_date,
                request_id=row.id,
                actor_user_id=ctx.user_id,
                payload={
                    "kind": "work",
                    "start": _instant(row.start_date, row.entry_time, tz).isoformat(),
                    "end": _instant(row.start_date, row.exit_time, tz).isoformat(),
                    "reason": row.reason,
                },
            )
        if row.kind in ("correction", "leave"):
            days = [
                row.start_date + timedelta(days=i)
                for i in range((row.end_date - row.start_date).days + 1)
            ]
            await recompute_days(ctx.db, target, days, office=office)
    await ctx.audit(f"request.{data.action}", "request", row.id, before, snapshot(row))
    return to_out(row, target.name)


async def cancel(ctx: Ctx, request_id: uuid.UUID) -> RequestOut:
    row = (
        await ctx.db.execute(
            select(Request).where(and_(Request.id == request_id, Request.user_id == ctx.user_id))
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Request")
    await lock_user(ctx.db, ctx.org_id, ctx.user_id)
    row = (
        await ctx.db.execute(select(Request).where(Request.id == request_id).with_for_update())
    ).scalar_one()
    user = await get_user(ctx.db, ctx.user_id)
    office, tz = await _office_tz(ctx, user)
    today = utcnow().astimezone(ZoneInfo(tz)).date()
    was_approved = row.status == "approved"
    if row.status == "pending" or (
        was_approved and row.kind in ("leave", "wfh") and row.start_date >= today
    ):
        before = snapshot(row)
        row.status = "cancelled"
        await ctx.db.flush()
        if was_approved and row.kind == "leave":
            days = [
                row.start_date + timedelta(days=i)
                for i in range((row.end_date - row.start_date).days + 1)
            ]
            await recompute_days(ctx.db, user, days, office=office)
        await ctx.audit("request.cancel", "request", row.id, before, snapshot(row))
        return to_out(row, ctx.principal.name)
    raise conflict("request_not_cancellable", f"A {row.status} request cannot be cancelled")
