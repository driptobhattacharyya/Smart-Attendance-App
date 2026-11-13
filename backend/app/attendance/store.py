"""Database side of the attendance engine: appending events, locking, loading inputs and keeping
``attendance_days`` in step with the event log.

Concurrency: every write path first takes ``lock_user`` -- a transaction-scoped Postgres advisory
lock keyed on (org, user). Why an advisory lock rather than ``SELECT ... FOR UPDATE``:

* "no open session yet" is exactly the state a check-in must protect, and there is no row to lock
  for an absent session; a row lock on ``users`` would work but conflicts with every foreign-key
  insert that references the user (``FOR KEY SHARE``) and with unrelated updates of that row;
* the lock is released automatically at commit/rollback, so it is safe behind a transaction-mode
  pooler (no session state), and it can never leak;
* it serialises *all* attendance writes of one user (check-in, heartbeat, check-out, jobs,
  corrections, request approvals), which is what keeps the append-only log consistent: a second
  request waits, then sees the first one's committed events.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..core.security import utcnow
from ..people.models import User
from ..places.models import Office
from .engine import DayRecord, Event, OfficeInfo, SamplesSummary, compute_day
from .models import (
    CLOSE_TYPES,
    START_TYPES,
    AttendanceDay,
    AttendanceEvent,
    PresenceSample,
    Request,
)
from .rules import EffectivePolicy, policies_for_org, policy_for_day


async def lock_user(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"),
        {"key": f"attendance:{org_id}:{user_id}"},
    )


def office_info(office: Office | None) -> OfficeInfo:
    if office is None:
        return OfficeInfo("UTC")
    return OfficeInfo(office.timezone, office.work_start, office.work_end)


def local_date(instant: datetime, timezone: str) -> date:
    return instant.astimezone(ZoneInfo(timezone)).date()


def to_engine_event(e: AttendanceEvent) -> Event:
    return Event(
        id=e.id,
        type=e.type,
        occurred_at=e.occurred_at,
        recorded_at=e.recorded_at,
        session_id=e.session_id,
        payload=e.payload or {},
        voids_event_id=e.voids_event_id,
    )


async def append_event(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    type: str,
    occurred_at: datetime,
    source: str,
    day: date,
    session_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    voids_event_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
) -> AttendanceEvent:
    event = AttendanceEvent(
        id=event_id or uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        occurred_at=occurred_at,
        recorded_at=utcnow(),
        type=type,
        source=source,
        session_id=session_id,
        day=day,
        payload=payload or {},
        voids_event_id=voids_event_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    db.add(event)
    await db.flush()
    return event


# ----------------------------------------------------------------------- open sessions


def open_session_query(user_id: uuid.UUID | None = None):
    """Session-start events that are neither voided nor closed (by a non-voided close)."""
    s = aliased(AttendanceEvent)
    voided_start = aliased(AttendanceEvent)
    close = aliased(AttendanceEvent)
    voided_close = aliased(AttendanceEvent)
    stmt = select(s).where(
        s.type.in_(START_TYPES),
        ~exists().where(voided_start.voids_event_id == s.id),
        ~exists().where(
            and_(
                close.session_id == s.id,
                close.type.in_(CLOSE_TYPES),
                ~exists().where(voided_close.voids_event_id == close.id),
            )
        ),
    )
    if user_id is not None:
        stmt = stmt.where(s.user_id == user_id)
    return stmt, s


async def get_open_session(db: AsyncSession, user_id: uuid.UUID) -> AttendanceEvent | None:
    stmt, s = open_session_query(user_id)
    return (await db.execute(stmt.order_by(s.occurred_at.desc()).limit(1))).scalar_one_or_none()


async def last_presence_event(db: AsyncSession, session_id: uuid.UUID) -> AttendanceEvent | None:
    p = AttendanceEvent
    v = aliased(AttendanceEvent)
    return (
        await db.execute(
            select(p)
            .where(
                p.session_id == session_id,
                p.type == "presence_changed",
                ~exists().where(v.voids_event_id == p.id),
            )
            .order_by(p.occurred_at.desc(), p.recorded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def last_sample_at(db: AsyncSession, session_id: uuid.UUID) -> datetime | None:
    return (
        await db.execute(
            select(func.max(PresenceSample.at)).where(PresenceSample.session_id == session_id)
        )
    ).scalar_one()


async def session_activity(
    db: AsyncSession, start: AttendanceEvent
) -> tuple[str, str | None, datetime]:
    """``(state, reason, last_evidence_at)`` of an open session: its latest presence state and the
    newest sign of life (start, state change or heartbeat sample)."""
    last = await last_presence_event(db, start.id)
    state = (last.payload.get("state") if last else None) or "present"
    reason = last.payload.get("reason") if last else None
    sample = await last_sample_at(db, start.id)
    evidence = max(t for t in (start.occurred_at, last.occurred_at if last else None, sample) if t)
    return state, reason, evidence


async def close_session(
    db: AsyncSession,
    start: AttendanceEvent,
    *,
    at: datetime,
    type: str,
    source: str,
    reason: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> AttendanceEvent:
    body = dict(payload or {})
    if reason:
        body["reason"] = reason
    return await append_event(
        db,
        org_id=start.org_id,
        user_id=start.user_id,
        type=type,
        occurred_at=max(at, start.occurred_at),
        source=source,
        day=start.day,
        session_id=start.id,
        payload=body,
        actor_user_id=actor_user_id,
    )


# ------------------------------------------------------------------------ recompute


async def approved_request_days(
    db: AsyncSession, user_id: uuid.UUID, kind: str, first: date, last: date
) -> set[date]:
    rows = (
        await db.execute(
            select(Request.start_date, Request.end_date).where(
                Request.user_id == user_id,
                Request.kind == kind,
                Request.status == "approved",
                Request.start_date <= last,
                Request.end_date >= first,
            )
        )
    ).all()
    days: set[date] = set()
    for start, end in rows:
        d = max(start, first)
        while d <= min(end, last):
            days.add(d)
            d += timedelta(days=1)
    return days


async def recompute_days(
    db: AsyncSession,
    user: User,
    days: Iterable[date],
    *,
    now: datetime | None = None,
    office: Office | None = None,
    policies: Sequence | None = None,
    persist: bool = True,
) -> dict[date, DayRecord]:
    """Rebuild ``attendance_days`` rows of one user from the event log. Runs inside the caller's
    transaction. Days with no activity that are not over yet are not stored (a row would say
    "absent" for a day that has not happened)."""
    day_list = sorted(set(days))
    if not day_list:
        return {}
    now = now or utcnow()
    if office is None and user.office_id:
        office = (
            await db.execute(select(Office).where(Office.id == user.office_id))
        ).scalar_one_or_none()
    info = office_info(office)
    policies = policies if policies is not None else await policies_for_org(db)

    events = (
        (
            await db.execute(
                select(AttendanceEvent).where(
                    AttendanceEvent.user_id == user.id, AttendanceEvent.day.in_(day_list)
                )
            )
        )
        .scalars()
        .all()
    )
    by_day: dict[date, list[Event]] = defaultdict(list)
    starts: dict[date, list[uuid.UUID]] = defaultdict(list)
    for e in events:
        by_day[e.day].append(to_engine_event(e))
        if e.type in START_TYPES:
            starts[e.day].append(e.id)
    all_starts = [sid for ids in starts.values() for sid in ids]
    last_at: dict[uuid.UUID, datetime] = {}
    if all_starts:
        rows = await db.execute(
            select(PresenceSample.session_id, func.max(PresenceSample.at))
            .where(PresenceSample.session_id.in_(all_starts))
            .group_by(PresenceSample.session_id)
        )
        last_at = {sid: at for sid, at in rows}
    leave = await approved_request_days(db, user.id, "leave", day_list[0], day_list[-1])
    today = local_date(now, info.timezone)

    results: dict[date, DayRecord] = {}
    for day in day_list:
        policy: EffectivePolicy = policy_for_day(list(policies), day)
        record = compute_day(
            by_day.get(day, []),
            SamplesSummary(last_at),
            policy.rules,
            info,
            day,
            now=now,
            on_leave=day in leave,
        )
        results[day] = record
        if not persist:
            continue
        if not record.has_activity and record.status == "absent" and day >= today:
            await db.execute(
                delete(AttendanceDay).where(
                    AttendanceDay.user_id == user.id, AttendanceDay.day == day
                )
            )
            continue
        values = dict(
            org_id=user.org_id,
            user_id=user.id,
            day=day,
            first_in=record.first_in,
            last_out=record.last_out,
            worked_minutes=record.worked_minutes,
            away_minutes=record.away_minutes,
            break_minutes=record.break_minutes,
            late_minutes=record.late_minutes,
            early_leave_minutes=record.early_leave_minutes,
            overtime_minutes=record.overtime_minutes,
            status=record.status,
            flags=record.flags,
            sessions=[s.to_json() for s in record.sessions],
            office_id=user.office_id,
            policy_id=policy.id,
            computed_at=now,
        )
        stmt = insert(AttendanceDay).values(**values)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["org_id", "user_id", "day"],
                set_={k: v for k, v in values.items() if k not in ("org_id", "user_id", "day")},
            )
        )
    return results


async def compute_many(
    db: AsyncSession,
    targets: Sequence[tuple[User, Office | None, date]],
    now: datetime,
    policies: Sequence | None = None,
) -> dict[tuple[uuid.UUID, date], DayRecord]:
    """Compute (never store) many (user, day) records with a constant number of queries. Used by
    the live board, dashboard and timesheet, where one query per employee would not scale."""
    if not targets:
        return {}
    policies = list(policies if policies is not None else await policies_for_org(db))
    user_ids = {u.id for u, _, _ in targets}
    days = {d for _, _, d in targets}
    events = (
        (
            await db.execute(
                select(AttendanceEvent).where(
                    AttendanceEvent.user_id.in_(user_ids), AttendanceEvent.day.in_(days)
                )
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[tuple[uuid.UUID, date], list[Event]] = defaultdict(list)
    starts: list[uuid.UUID] = []
    for e in events:
        grouped[(e.user_id, e.day)].append(to_engine_event(e))
        if e.type in START_TYPES:
            starts.append(e.id)
    last_at: dict[uuid.UUID, datetime] = {}
    if starts:
        rows = await db.execute(
            select(PresenceSample.session_id, func.max(PresenceSample.at))
            .where(PresenceSample.session_id.in_(starts))
            .group_by(PresenceSample.session_id)
        )
        last_at = {sid: at for sid, at in rows}
    leave: dict[uuid.UUID, set[date]] = defaultdict(set)
    leave_rows = await db.execute(
        select(Request.user_id, Request.start_date, Request.end_date).where(
            Request.user_id.in_(user_ids),
            Request.kind == "leave",
            Request.status == "approved",
            Request.start_date <= max(days),
            Request.end_date >= min(days),
        )
    )
    for uid, first, last in leave_rows:
        d = first
        while d <= last:
            leave[uid].add(d)
            d += timedelta(days=1)
    out: dict[tuple[uuid.UUID, date], DayRecord] = {}
    for user, office, day in targets:
        rules = policy_for_day(policies, day).rules
        out[(user.id, day)] = compute_day(
            grouped.get((user.id, day), []),
            SamplesSummary(last_at),
            rules,
            office_info(office),
            day,
            now=now,
            on_leave=day in leave.get(user.id, ()),
        )
    return out


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one()


def dt_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
