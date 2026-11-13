"""Manager / admin read APIs: presence board, dashboard, day and session detail, timesheet, CSV.

Everything is scoped by permission: ``attendance.view.org`` sees the organization,
``attendance.view.team`` the caller's reporting tree; anyone else is refused, and a person outside
the scope reads as 404.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased

from ..auth.deps import Ctx
from ..core.errors import AppError, bad_request, forbidden, not_found
from ..core.pagination import keyset_page
from ..core.security import utcnow
from ..people.models import User
from ..places.models import DeskDevice, Office
from ..rbac.permissions import (
    ATTENDANCE_VIEW_ORG,
    ATTENDANCE_VIEW_TEAM,
    REPORTS_EXPORT,
    REQUESTS_APPROVE_ORG,
    REQUESTS_APPROVE_TEAM,
)
from ..rbac.scope import is_in_team, team_user_ids
from .models import (
    AttendanceDay,
    AttendanceEvent,
    FaceEnrollment,
    PresenceSample,
    Request,
)
from .rules import policies_for_org, policy_for_day
from .schemas import EventOut
from .store import (
    compute_many,
    local_date,
    office_info,
    open_session_query,
)

STATUS_ORDER = ("present", "remote", "away", "stale", "outside", "not_checked_in", "on_leave")
MAX_RANGE_DAYS = 93
MAX_EXPORT_ROWS = 100_000
OUTSIDE_REASONS = ("outside", "mock_location", "rooted_device")


def scope_filter(ctx: Ctx):
    """None = whole organization; else a user-id subquery. Raises for callers with neither."""
    if ctx.has(ATTENDANCE_VIEW_ORG):
        return None
    if ctx.has(ATTENDANCE_VIEW_TEAM):
        return team_user_ids(ctx.user_id)
    raise forbidden("You do not have permission to do this")


def scoped_users(ctx: Ctx, *, active_only: bool = True):
    stmt = select(User)
    scope = scope_filter(ctx)
    if scope is not None:
        stmt = stmt.where(User.id.in_(scope))
    if active_only:
        stmt = stmt.where(User.status == "active")
    return stmt


async def _visible_user(ctx: Ctx, user_id: uuid.UUID) -> User:
    """Self, or anyone in scope; everything else is 404."""
    user = (await ctx.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise not_found("Person")
    if user.id == ctx.user_id:
        return user
    if ctx.has(ATTENDANCE_VIEW_ORG):
        return user
    if ctx.has(ATTENDANCE_VIEW_TEAM) and await is_in_team(ctx.db, ctx.user_id, user.id):
        return user
    raise not_found("Person")


async def _offices(ctx: Ctx) -> dict[uuid.UUID, Office]:
    return {o.id: o for o in (await ctx.db.execute(select(Office))).scalars()}


def _tz(office: Office | None) -> str:
    return office_info(office).timezone


# ------------------------------------------------------------------------ presence board


async def presence_board(
    ctx: Ctx,
    *,
    office_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    now = utcnow()
    stmt = scoped_users(ctx)
    if office_id:
        stmt = stmt.where(User.office_id == office_id)
    if department_id:
        stmt = stmt.where(User.department_id == department_id)
    users = list((await ctx.db.execute(stmt.order_by(func.lower(User.name)))).scalars())
    if not users:
        return []
    ids = [u.id for u in users]
    offices = await _offices(ctx)
    policies = await policies_for_org(ctx.db)

    enrolled = set(
        (
            await ctx.db.execute(
                select(FaceEnrollment.user_id).where(FaceEnrollment.user_id.in_(ids))
            )
        ).scalars()
    )
    open_stmt, s = open_session_query()
    opened: dict[uuid.UUID, AttendanceEvent] = {}
    for e in (
        await ctx.db.execute(open_stmt.where(s.user_id.in_(ids)).order_by(s.occurred_at.asc()))
    ).scalars():
        opened[e.user_id] = e  # the newest wins
    session_ids = [e.id for e in opened.values()]

    last_presence: dict[uuid.UUID, AttendanceEvent] = {}
    last_sample: dict[uuid.UUID, PresenceSample] = {}
    if session_ids:
        for e in (
            await ctx.db.execute(
                select(AttendanceEvent)
                .where(
                    AttendanceEvent.session_id.in_(session_ids),
                    AttendanceEvent.type == "presence_changed",
                )
                .order_by(AttendanceEvent.occurred_at.asc(), AttendanceEvent.recorded_at.asc())
            )
        ).scalars():
            last_presence[e.session_id] = e
        for smp in (
            await ctx.db.execute(
                select(PresenceSample)
                .where(PresenceSample.session_id.in_(session_ids))
                .distinct(PresenceSample.session_id)
                .order_by(PresenceSample.session_id, PresenceSample.at.desc())
            )
        ).scalars():
            last_sample[smp.session_id] = smp

    # Users not checked in: was the last thing today a system close for leaving the area?
    closed_outside: set[uuid.UUID] = set()
    not_open = [i for i in ids if i not in opened]
    if not_open:
        latest = (
            await ctx.db.execute(
                select(AttendanceEvent)
                .where(
                    AttendanceEvent.user_id.in_(not_open),
                    AttendanceEvent.type.in_(
                        ("check_in", "remote_check_in", "check_out", "auto_check_out")
                    ),
                    AttendanceEvent.occurred_at > now - timedelta(hours=24),
                )
                .distinct(AttendanceEvent.user_id)
                .order_by(AttendanceEvent.user_id, AttendanceEvent.occurred_at.desc())
            )
        ).scalars()
        closed_outside = {
            e.user_id
            for e in latest
            if e.type == "auto_check_out" and e.payload.get("reason") in OUTSIDE_REASONS
        }

    targets = []
    today_of: dict[uuid.UUID, date] = {}
    for u in users:
        office = offices.get(u.office_id) if u.office_id else None
        today_of[u.id] = local_date(now, _tz(office))
        targets.append((u, office, today_of[u.id]))
    records = await compute_many(ctx.db, targets, now, policies)
    leave_ids: set[uuid.UUID] = set()
    leave_rows = await ctx.db.execute(
        select(Request.user_id, Request.start_date, Request.end_date).where(
            Request.user_id.in_(ids),
            Request.kind == "leave",
            Request.status == "approved",
            Request.start_date <= max(today_of.values()),
            Request.end_date >= min(today_of.values()),
        )
    )
    for uid, first, last in leave_rows:
        if first <= today_of[uid] <= last:
            leave_ids.add(uid)

    rows: list[dict[str, Any]] = []
    for u in users:
        start = opened.get(u.id)
        record = records[(u.id, today_of[u.id])]
        row: dict[str, Any] = {
            "user_id": u.id,
            "name": u.name,
            "employee_code": u.employee_code,
            "office_id": u.office_id,
            "department_id": u.department_id,
            "face_enrolled": u.id in enrolled,
            "today_minutes": record.worked_minutes,
            "checked_in": start is not None,
            "session_id": start.id if start else None,
            "mode": None,
            "since": start.occurred_at if start else None,
            "last_seen_at": None,
            "best_rssi": None,
            "reason": None,
        }
        if start is not None:
            presence = last_presence.get(start.id)
            sample = last_sample.get(start.id)
            state = (presence.payload.get("state") if presence else None) or "present"
            reason = presence.payload.get("reason") if presence else "check_in"
            seen = max(
                t
                for t in (
                    start.occurred_at,
                    presence.occurred_at if presence else None,
                    sample.at if sample else None,
                )
                if t
            )
            if sample is not None and (presence is None or sample.at >= presence.occurred_at):
                reason = sample.reason or reason
                row["best_rssi"] = sample.best_rssi
            grace = policy_for_day(policies, start.day).rules.heartbeat_grace_minutes
            row["mode"] = "remote" if start.type == "remote_check_in" else "office"
            row["last_seen_at"] = seen
            row["reason"] = reason
            if now - seen > timedelta(minutes=grace):
                row["status"] = "stale"
            elif row["mode"] == "remote":
                row["status"] = "remote"
            else:
                row["status"] = state
        elif u.id in leave_ids:
            row["status"], row["reason"] = "on_leave", "approved_leave"
        elif u.id in closed_outside:
            row["status"], row["reason"] = "outside", "left_geofence"
        else:
            row["status"], row["reason"] = "not_checked_in", None
        rows.append(row)
    if status:
        rows = [r for r in rows if r["status"] == status]
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), r["name"].casefold()))
    return rows


# --------------------------------------------------------------------------- dashboard


async def dashboard(ctx: Ctx, day: date | None) -> dict[str, Any]:
    now = utcnow()
    scope = scope_filter(ctx)
    users = list((await ctx.db.execute(scoped_users(ctx))).scalars())
    ids = [u.id for u in users]
    offices = await _offices(ctx)
    first_office = min(offices.values(), key=lambda o: o.name, default=None)
    today = local_date(now, _tz(first_office))
    day = day or today
    headcount = {k: 0 for k in STATUS_ORDER}
    headcount["total"] = len(users)
    live_by_user: dict[uuid.UUID, dict[str, Any]] = {}
    if day == today:
        for r in await presence_board(ctx):
            headcount[r["status"]] += 1
            live_by_user[r["user_id"]] = r
    elif ids:
        rows = await ctx.db.execute(
            select(AttendanceDay.user_id, AttendanceDay.status).where(
                AttendanceDay.day == day, AttendanceDay.user_id.in_(ids)
            )
        )
        seen = {}
        for uid, st in rows:
            seen[uid] = st
        for u in users:
            st = seen.get(u.id)
            key = (
                "present"
                if st in ("present", "half_day", "incomplete")
                else "on_leave"
                if st == "on_leave"
                else "not_checked_in"
            )
            headcount[key] += 1

    first = day - timedelta(days=13)
    trend: dict[date, dict[str, int]] = {
        first + timedelta(days=i): {"worked_minutes": 0, "away_minutes": 0, "people": 0}
        for i in range(14)
    }
    if ids:
        rows = await ctx.db.execute(
            select(
                AttendanceDay.day,
                func.coalesce(func.sum(AttendanceDay.worked_minutes), 0),
                func.coalesce(func.sum(AttendanceDay.away_minutes), 0),
                func.count().filter(AttendanceDay.status.in_(("present", "half_day"))),
            )
            .where(AttendanceDay.user_id.in_(ids), AttendanceDay.day.between(first, day))
            .group_by(AttendanceDay.day)
        )
        for d, worked, away, people in rows:
            trend[d] = {"worked_minutes": int(worked), "away_minutes": int(away), "people": people}
    if day == today and live_by_user:  # stored rows lag behind open sessions: use the live view
        trend[day] = {
            "worked_minutes": sum(r["today_minutes"] for r in live_by_user.values()),
            "away_minutes": trend[day]["away_minutes"],
            "people": sum(1 for r in live_by_user.values() if r["today_minutes"] > 0),
        }

    late = []
    if ids:
        late_rows = await ctx.db.execute(
            select(AttendanceDay, User.name)
            .join(User, User.id == AttendanceDay.user_id)
            .where(
                AttendanceDay.user_id.in_(ids),
                AttendanceDay.day == day,
                AttendanceDay.late_minutes > 0,
            )
            .order_by(AttendanceDay.first_in.desc())
            .limit(10)
        )
        late = [
            {
                "user_id": d.user_id,
                "name": name,
                "first_in": d.first_in,
                "late_minutes": d.late_minutes,
            }
            for d, name in late_rows
        ]

    pending = None
    if ctx.has(REQUESTS_APPROVE_ORG):
        pending_scope = None
    elif ctx.has(REQUESTS_APPROVE_TEAM):
        pending_scope = Request.user_id.in_(team_user_ids(ctx.user_id))
    else:
        pending_scope = False
    if pending_scope is not False:
        q = select(func.count()).select_from(Request).where(Request.status == "pending")
        if pending_scope is not None:
            q = q.where(pending_scope)
        pending = (await ctx.db.execute(q)).scalar_one()

    modules = ctx.org.modules
    without_face = None
    if modules.get("face") and ids:
        without_face = (
            await ctx.db.execute(
                select(func.count())
                .select_from(User)
                .where(
                    User.id.in_(ids),
                    ~exists().where(FaceEnrollment.user_id == User.id),
                )
            )
        ).scalar_one()
    without_desk = None
    if (modules.get("proximity") or modules.get("desk_app")) and ids:
        mine = aliased(DeskDevice)
        shared = aliased(DeskDevice)
        without_desk = (
            await ctx.db.execute(
                select(func.count())
                .select_from(User)
                .where(
                    User.id.in_(ids),
                    ~exists().where(mine.assigned_user_id == User.id, mine.active.is_(True)),
                    ~exists().where(
                        shared.assigned_user_id.is_(None),
                        shared.active.is_(True),
                        or_(shared.office_id.is_(None), shared.office_id == User.office_id),
                    ),
                )
            )
        ).scalar_one()
    unpaired = None
    if scope is None and modules.get("desk_app"):
        unpaired = (
            await ctx.db.execute(
                select(func.count())
                .select_from(DeskDevice)
                .where(
                    DeskDevice.kind == "desk_app",
                    DeskDevice.active.is_(True),
                    DeskDevice.paired_at.is_(None),
                )
            )
        ).scalar_one()
    return {
        "date": day,
        "headcount": headcount,
        "daily": [{"date": d, **v} for d, v in sorted(trend.items())],
        "late_today": late,
        "pending_requests": pending,
        "setup": {
            "employees_without_face": without_face,
            "employees_without_desk_device": without_desk,
            "unpaired_desk_apps": unpaired,
        },
    }


# -------------------------------------------------------------------- day / session detail


async def _events_out(ctx: Ctx, events: list[AttendanceEvent]) -> list[EventOut]:
    if not events:
        return []
    voided = set(
        (
            await ctx.db.execute(
                select(AttendanceEvent.voids_event_id).where(
                    AttendanceEvent.voids_event_id.in_([e.id for e in events])
                )
            )
        ).scalars()
    )
    out = []
    for e in events:
        item = EventOut.model_validate(e)
        item.voided = e.id in voided
        out.append(item)
    return out


async def day_detail(ctx: Ctx, user_id: uuid.UUID, day: date) -> dict[str, Any]:
    user = await _visible_user(ctx, user_id)
    office = (
        (
            await ctx.db.execute(select(Office).where(Office.id == user.office_id))
        ).scalar_one_or_none()
        if user.office_id
        else None
    )
    events = list(
        (
            await ctx.db.execute(
                select(AttendanceEvent)
                .where(AttendanceEvent.user_id == user.id, AttendanceEvent.day == day)
                .order_by(AttendanceEvent.occurred_at, AttendanceEvent.recorded_at)
            )
        ).scalars()
    )
    row = (
        await ctx.db.execute(
            select(AttendanceDay).where(AttendanceDay.user_id == user.id, AttendanceDay.day == day)
        )
    ).scalar_one_or_none()
    live = (await compute_many(ctx.db, [(user, office, day)], utcnow()))[(user.id, day)]
    session_ids = [e.id for e in events if e.type in ("check_in", "remote_check_in")]
    samples = []
    if session_ids:
        samples = list(
            (
                await ctx.db.execute(
                    select(PresenceSample)
                    .where(PresenceSample.session_id.in_(session_ids))
                    .order_by(PresenceSample.at)
                    .limit(1000)
                )
            ).scalars()
        )
    return {
        "user": {"id": user.id, "name": user.name, "employee_code": user.employee_code},
        "day": day,
        "stored": _day_dict(row) if row else None,
        "calculated": _record_dict(live),
        "events": await _events_out(ctx, events),
        "samples": [_sample_dict(s) for s in samples],
    }


async def session_detail(ctx: Ctx, session_id: uuid.UUID) -> dict[str, Any]:
    start = (
        await ctx.db.execute(
            select(AttendanceEvent).where(
                AttendanceEvent.id == session_id,
                AttendanceEvent.type.in_(("check_in", "remote_check_in")),
            )
        )
    ).scalar_one_or_none()
    if start is None:
        raise not_found("Session")
    user = await _visible_user(ctx, start.user_id)
    events = list(
        (
            await ctx.db.execute(
                select(AttendanceEvent)
                .where(AttendanceEvent.session_id == session_id)
                .order_by(AttendanceEvent.occurred_at, AttendanceEvent.recorded_at)
            )
        ).scalars()
    )
    samples = list(
        (
            await ctx.db.execute(
                select(PresenceSample)
                .where(PresenceSample.session_id == session_id)
                .order_by(PresenceSample.at)
                .limit(2000)
            )
        ).scalars()
    )
    return {
        "user": {"id": user.id, "name": user.name, "employee_code": user.employee_code},
        "session_id": session_id,
        "day": start.day,
        "events": await _events_out(ctx, events),
        "samples": [_sample_dict(s) for s in samples],
    }


def _sample_dict(s: PresenceSample) -> dict[str, Any]:
    return {
        "at": s.at,
        "state": s.state,
        "reason": s.reason,
        "distance_m": s.distance_m,
        "best_rssi": s.best_rssi,
        "accuracy_m": s.accuracy_m,
        "mock_location": s.mock_location,
    }


def _day_dict(d: AttendanceDay) -> dict[str, Any]:
    return {
        c: getattr(d, c)
        for c in (
            "day first_in last_out worked_minutes away_minutes break_minutes late_minutes "
            "early_leave_minutes overtime_minutes status flags sessions computed_at"
        ).split()
    }


def _record_dict(r) -> dict[str, Any]:
    return {
        "day": r.day,
        "first_in": r.first_in,
        "last_out": r.last_out,
        "worked_minutes": r.worked_minutes,
        "away_minutes": r.away_minutes,
        "break_minutes": r.break_minutes,
        "late_minutes": r.late_minutes,
        "early_leave_minutes": r.early_leave_minutes,
        "overtime_minutes": r.overtime_minutes,
        "status": r.status,
        "flags": r.flags,
        "sessions": [s.to_json() for s in r.sessions],
    }


# ---------------------------------------------------------------------------- timesheet


def check_range(start: date, end: date) -> None:
    if end < start:
        raise bad_request("invalid_range", "end is before start")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise bad_request("range_too_large", f"At most {MAX_RANGE_DAYS} days at a time")


async def timesheet(
    ctx: Ctx,
    *,
    start: date,
    end: date,
    office_id: uuid.UUID | None,
    department_id: uuid.UUID | None,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    check_range(start, end)
    stmt = scoped_users(ctx)
    if office_id:
        stmt = stmt.where(User.office_id == office_id)
    if department_id:
        stmt = stmt.where(User.department_id == department_id)
    users, next_cursor = await keyset_page(
        ctx.db, stmt, [func.lower(User.name), User.id], limit=limit, cursor=cursor
    )
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    offices = await _offices(ctx)
    now = utcnow()
    stored: dict[tuple[uuid.UUID, date], AttendanceDay] = {}
    if users:
        for d in (
            await ctx.db.execute(
                select(AttendanceDay).where(
                    AttendanceDay.user_id.in_([u.id for u in users]),
                    AttendanceDay.day.between(start, end),
                )
            )
        ).scalars():
            stored[(d.user_id, d.day)] = d
    missing = []
    for u in users:
        office = offices.get(u.office_id) if u.office_id else None
        today = local_date(now, _tz(office))
        for d in days:
            if (u.id, d) not in stored and d < today:
                missing.append((u, office, d))
    filled = await compute_many(ctx.db, missing, now)
    rows = []
    for u in users:
        cells: dict[str, Any] = {}
        totals = {
            "worked_minutes": 0,
            "present": 0,
            "half_day": 0,
            "absent": 0,
            "on_leave": 0,
            "late": 0,
        }
        for d in days:
            row = stored.get((u.id, d))
            if row is not None:
                cell = {
                    "status": row.status,
                    "worked_minutes": row.worked_minutes,
                    "first_in": row.first_in,
                    "last_out": row.last_out,
                    "late_minutes": row.late_minutes,
                    "flags": row.flags,
                }
            elif (u.id, d) in filled:
                r = filled[(u.id, d)]
                cell = {
                    "status": r.status,
                    "worked_minutes": 0,
                    "first_in": None,
                    "last_out": None,
                    "late_minutes": 0,
                    "flags": r.flags,
                }
            else:
                cell = {
                    "status": None,
                    "worked_minutes": 0,
                    "first_in": None,
                    "last_out": None,
                    "late_minutes": 0,
                    "flags": [],
                }
            cells[d.isoformat()] = cell
            totals["worked_minutes"] += cell["worked_minutes"]
            if cell["status"] in ("present", "half_day", "absent", "on_leave"):
                totals[cell["status"]] += 1
            if cell["late_minutes"]:
                totals["late"] += 1
        rows.append(
            {
                "user_id": u.id,
                "name": u.name,
                "employee_code": u.employee_code,
                "cells": cells,
                "totals": totals,
            }
        )
    return {"start": start, "end": end, "days": days, "rows": rows, "next_cursor": next_cursor}


# ------------------------------------------------------------------------------- CSV

CSV_COLUMNS = [
    "employee_code",
    "name",
    "office",
    "date",
    "status",
    "first_in",
    "last_out",
    "worked_minutes",
    "away_minutes",
    "late_minutes",
    "early_leave_minutes",
    "overtime_minutes",
    "flags",
]


def csv_text(value: Any) -> str:
    """Stop spreadsheets running a name like ``=HYPERLINK(...)`` as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


async def export_csv(
    ctx: Ctx,
    *,
    start: date,
    end: date,
    office_id: uuid.UUID | None,
    department_id: uuid.UUID | None,
) -> str:
    ctx.require(REPORTS_EXPORT)
    check_range(start, end)
    stmt = (
        select(AttendanceDay, User, Office)
        .join(User, User.id == AttendanceDay.user_id)
        .outerjoin(Office, Office.id == User.office_id)
        .where(AttendanceDay.day.between(start, end))
    )
    scope = scope_filter(ctx)
    if scope is not None:
        stmt = stmt.where(User.id.in_(scope))
    if office_id:
        stmt = stmt.where(User.office_id == office_id)
    if department_id:
        stmt = stmt.where(User.department_id == department_id)
    stmt = stmt.order_by(func.lower(User.name), User.id, AttendanceDay.day).limit(
        MAX_EXPORT_ROWS + 1
    )
    result = (await ctx.db.execute(stmt)).all()
    if len(result) > MAX_EXPORT_ROWS:
        raise AppError(413, "export_too_large", "Narrow the date range or filters")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)

    def hhmm(instant: datetime | None, tz: ZoneInfo) -> str:
        return instant.astimezone(tz).strftime("%H:%M") if instant else ""

    for d, u, office in result:
        tz = ZoneInfo(_tz(office))
        writer.writerow(
            [
                csv_text(u.employee_code),
                csv_text(u.name),
                csv_text(office.name if office else ""),
                d.day.isoformat(),
                d.status,
                hhmm(d.first_in, tz),
                hhmm(d.last_out, tz),
                d.worked_minutes,
                d.away_minutes,
                d.late_minutes,
                d.early_leave_minutes,
                d.overtime_minutes,
                csv_text(" ".join(d.flags)),
            ]
        )
    await ctx.audit(
        "report.export",
        "attendance",
        None,
        after={
            "start": start,
            "end": end,
            "rows": len(result),
            "office_id": office_id,
            "department_id": department_id,
        },
    )
    return out.getvalue()


__all__ = ["and_"]
