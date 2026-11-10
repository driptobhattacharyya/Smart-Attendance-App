"""Check-in, heartbeat, check-out, status and the caller's own history.

Every state-changing function runs inside the request's tenant transaction, takes the per-user
advisory lock first (``store.lock_user``), appends events and recomputes the affected
``attendance_days`` row before the transaction commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Text, cast, exists, select
from sqlalchemy.orm import aliased

from ..audit.service import write_audit
from ..auth.deps import Ctx
from ..core.errors import AppError, bad_request, conflict, error_body, forbidden
from ..core.idempotency import run_idempotent
from ..core.security import utcnow
from ..people.models import User
from ..places.models import Office
from .engine import DayRecord
from .lifecycle import expire_if_needed
from .models import (
    START_TYPES,
    AttendanceDay,
    AttendanceEvent,
    FaceEnrollment,
    PresenceSample,
)
from .proximity import (
    decide_state,
    evaluate_for_user,
    load_candidates,
    presence_reason,
)
from .rules import PolicyRules, policies_for_org, policy_for_day
from .schemas import (
    CheckInIn,
    CheckInOut,
    CheckOutIn,
    CheckOutOut,
    DayOut,
    HeartbeatIn,
    HeartbeatOut,
    MeOut,
    SessionOut,
    StatusOut,
)
from .store import (
    append_event,
    approved_request_days,
    close_session,
    get_open_session,
    get_user,
    local_date,
    lock_user,
    office_info,
    recompute_days,
    session_activity,
)
from .trust import (
    check_device,
    consume_nonce,
    distance_to,
    location_problem,
    require_location,
)

# Rejections worth a durable audit row (they are security signals, not user mistakes).
SECURITY_CODES = {"mock_location", "rooted_device", "integrity_failed", "integrity_required"}
DESK_CODES = {
    "no_signal": "desk_not_near",
    "weak_signal": "desk_not_near",
    "desk_locked": "desk_locked",
    "desk_idle": "desk_idle",
    "desk_offline": "desk_offline",
}
DESK_MESSAGES = {
    "desk_not_near": "Your desk is not nearby",
    "desk_locked": "Your desk computer is locked; unlock it to check in",
    "desk_idle": "Your desk computer has been idle for too long; use it, then try again",
    "desk_offline": "Your desk computer is not reporting in; check that Attendify Desk is running",
}
MAX_RANGE_DAYS = 93


@dataclass
class Env:
    user: User
    office: Office | None
    tz: str
    policies: list
    now: datetime
    today: date
    rules: PolicyRules
    policy_id: uuid.UUID | None


async def load_env(ctx: Ctx, now: datetime | None = None) -> Env:
    now = now or utcnow()
    user = await get_user(ctx.db, ctx.user_id)
    office = None
    if user.office_id:
        office = (
            await ctx.db.execute(select(Office).where(Office.id == user.office_id))
        ).scalar_one_or_none()
    info = office_info(office)
    policies = await policies_for_org(ctx.db)
    today = local_date(now, info.timezone)
    effective = policy_for_day(policies, today)
    return Env(user, office, info.timezone, policies, now, today, effective.rules, effective.id)


async def _with_key(
    ctx: Ctx,
    key: str | None,
    scope: str,
    body: Any,
    handler: Callable[[], Awaitable[tuple[int, Any]]],
) -> tuple[int, Any]:
    if not key:
        return await handler()
    status, response, _ = await run_idempotent(
        ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        key=key,
        scope=scope,
        # The key space is per organization: bind the caller so one user can never replay
        # another user's stored response.
        payload={"user": str(ctx.user_id), "body": body},
        handler=handler,
    )
    return status, response


async def record_rejection(ctx: Ctx, code: str, **details: Any) -> None:
    """Persist an audit row for a security-relevant rejection *outside* the request transaction
    (which is about to roll back)."""
    if ctx.database is None:
        return
    async with ctx.database.tenant_session(ctx.org_id) as session:
        await write_audit(
            session,
            org_id=ctx.org_id,
            actor_user_id=ctx.user_id,
            action="attendance.rejected",
            target_type="user",
            target_id=ctx.user_id,
            after={"code": code, **details},
            ip=ctx.ip,
            user_agent=ctx.user_agent,
        )


# ---------------------------------------------------------------------------- helpers


def session_out(
    info: dict[str, Any], *, state: str | None = None, reason: str | None = None, seen=None
) -> SessionOut:
    return SessionOut(
        id=uuid.UUID(info["id"]),
        mode=info["mode"],
        started_at=info["started_at"],
        ended_at=info["ended_at"],
        open=info["open"],
        state=state,
        reason=reason,
        last_seen_at=seen,
        worked_minutes=info["worked_minutes"],
        away_minutes=info["away_minutes"],
    )


def day_out(record: DayRecord) -> DayOut:
    return DayOut(
        day=record.day,
        status=record.status,
        first_in=record.first_in,
        last_out=record.last_out,
        worked_minutes=record.worked_minutes,
        away_minutes=record.away_minutes,
        break_minutes=record.break_minutes,
        late_minutes=record.late_minutes,
        early_leave_minutes=record.early_leave_minutes,
        overtime_minutes=record.overtime_minutes,
        flags=record.flags,
        sessions=[s.to_json() for s in record.sessions],
    )


def _session_info(record: DayRecord, session_id: uuid.UUID) -> dict[str, Any]:
    return next(s.to_json() for s in record.sessions if s.id == session_id)


async def find_face_event(ctx: Ctx, user_id: uuid.UUID, now: datetime) -> AttendanceEvent | None:
    """The newest ``face_verified`` event within the TTL that no check-in has consumed yet."""
    ttl = timedelta(minutes=ctx.settings.face_verification_ttl_minutes)
    f = AttendanceEvent
    consumer = aliased(AttendanceEvent)
    voider = aliased(AttendanceEvent)
    return (
        await ctx.db.execute(
            select(f)
            .where(
                f.user_id == user_id,
                f.type == "face_verified",
                f.occurred_at >= now - ttl,
                ~exists().where(voider.voids_event_id == f.id),
                ~exists().where(
                    consumer.type.in_(START_TYPES),
                    consumer.payload["face_event_id"].astext == cast(f.id, Text),
                ),
            )
            .order_by(f.occurred_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def wfh_allowed(ctx: Ctx, env: Env) -> bool:
    if not ctx.org.modules.get("wfh"):
        return False
    if env.today.weekday() in env.rules.wfh_weekdays:
        return True
    return env.today in await approved_request_days(
        ctx.db, env.user.id, "wfh", env.today, env.today
    )


def _requirements(ctx: Ctx, rules: PolicyRules) -> dict[str, bool]:
    m = ctx.org.modules
    return {
        "face": bool(m.get("face") and rules.require_face),
        "geofence": bool(m.get("geofence") and rules.require_geofence),
        "proximity": bool(m.get("proximity") and rules.require_proximity),
    }


# ------------------------------------------------------------------------------ status


async def status(ctx: Ctx) -> StatusOut:
    env = await load_env(ctx)
    start = await get_open_session(ctx.db, ctx.user_id)
    records = await recompute_days(
        ctx.db,
        env.user,
        {env.today, *([start.day] if start else [])},
        now=env.now,
        office=env.office,
        policies=env.policies,
        persist=False,
    )
    session = None
    if start is not None:
        state, reason, evidence = await session_activity(ctx.db, start)
        info = _session_info(records[start.day], start.id)
        session = session_out(info, state=state, reason=reason, seen=evidence)
    face_event = await find_face_event(ctx, ctx.user_id, env.now)
    enrolled = (
        await ctx.db.execute(select(FaceEnrollment.id).where(FaceEnrollment.user_id == ctx.user_id))
    ).first() is not None
    ttl = timedelta(minutes=ctx.settings.face_verification_ttl_minutes)
    return StatusOut(
        checked_in=start is not None,
        session=session,
        today=day_out(records[env.today]),
        face_verified_until=face_event.occurred_at + ttl if face_event else None,
        face_enrolled=enrolled,
        presence=session.state if session else None,
        today_minutes=records[env.today].worked_minutes,
        heartbeat_interval_seconds=ctx.settings.heartbeat_interval_seconds,
        wfh_allowed=await wfh_allowed(ctx, env),
        requirements=_requirements(ctx, env.rules),
    )


# ---------------------------------------------------------------------------- check-in


async def check_in(ctx: Ctx, data: CheckInIn, key: str | None) -> tuple[int, Any]:
    async def handler() -> tuple[int, Any]:
        out = await _check_in(ctx, data)
        return 201, out.model_dump(mode="json")

    return await _with_key(ctx, key, "attendance.check_in", data.model_dump(mode="json"), handler)


async def _reject(ctx: Ctx, error: AppError) -> AppError:
    if error.code in SECURITY_CODES:
        await record_rejection(ctx, error.code, **error.details)
    return error


async def _check_in(ctx: Ctx, data: CheckInIn) -> CheckInOut:
    now = utcnow()
    await lock_user(ctx.db, ctx.org_id, ctx.user_id)
    env = await load_env(ctx, now)
    rules, modules = env.rules, ctx.org.modules

    open_session = await get_open_session(ctx.db, ctx.user_id)
    if open_session is not None:
        if await expire_if_needed(ctx.db, open_session, now, env.policies) is None:
            raise conflict(
                "already_checked_in", "You are already checked in", session_id=open_session.id
            )
        await recompute_days(ctx.db, env.user, [open_session.day], now=now, office=env.office)

    remote = data.mode == "remote"
    if remote:
        if not modules.get("wfh"):
            raise forbidden("The 'wfh' module is not enabled", "module_disabled", module="wfh")
        if not await wfh_allowed(ctx, env):
            raise forbidden("Working from home is not approved for today", "wfh_not_allowed")
    elif env.office is None or not env.office.active:
        raise conflict("no_office", "You are not assigned to an active office")

    # --- trust signals ---------------------------------------------------------------
    try:
        integrity = await check_device(
            data.device,
            block_rooted=bool(ctx.org.settings.get("block_rooted_devices", True)),
            verifier=getattr(ctx.state, "integrity_verifier", None),
            nonce_ok=lambda n: consume_nonce(ctx, n, now),
        )
    except AppError as exc:
        raise await _reject(ctx, exc) from exc

    location_payload: dict[str, Any] | None = None
    if not remote:
        geofence = bool(modules.get("geofence") and rules.require_geofence)
        loc = require_location(data.location) if geofence else data.location
        if loc is not None:
            problem = location_problem(loc, rules, now)
            if problem is not None:
                raise await _reject(ctx, problem.to_error())
            location_payload = {"lat": loc.lat, "lon": loc.lon, "accuracy_m": loc.accuracy_m}
            if env.office is not None:
                distance = distance_to(loc, env.office.latitude, env.office.longitude)
                location_payload["distance_m"] = round(distance, 1)
                if geofence and distance > env.office.radius_m:
                    raise forbidden(
                        f"You are {int(distance)} m from {env.office.name}; check-in is allowed "
                        f"within {env.office.radius_m} m",
                        "outside_geofence",
                        distance_m=round(distance, 1),
                        radius_m=env.office.radius_m,
                    )

    # --- face --------------------------------------------------------------------------
    face_event = None
    if modules.get("face") and rules.require_face:
        face_event = await find_face_event(ctx, ctx.user_id, now)
        if face_event is None:
            raise forbidden("Please verify your face first", "face_required")

    # --- desk proximity ----------------------------------------------------------------
    signal = None
    if not remote and modules.get("proximity") and rules.require_proximity:
        candidates = await load_candidates(ctx.db, ctx.settings, env.user)
        if not candidates:
            raise conflict("no_desk_device", "No desk device is configured for you; ask your admin")
        signal = evaluate_for_user(
            candidates, data.beacons, ctx.org.settings, rules, ctx.settings, now
        )
        if not signal.near:
            code = DESK_CODES[signal.reason]
            raise forbidden(DESK_MESSAGES[code], code, best_rssi=signal.best_rssi)

    # --- record ----------------------------------------------------------------------------
    session_id = uuid.uuid4()
    payload: dict[str, Any] = {
        "mode": data.mode,
        "integrity": integrity,
        "policy_id": str(env.policy_id) if env.policy_id else None,
    }
    if location_payload:
        payload["location"] = location_payload
    if data.device:
        payload["device"] = data.device.model_dump(
            include={"platform", "app_version", "rooted"}, mode="json"
        )
    if face_event is not None:
        payload["face_event_id"] = str(face_event.id)  # this is what "consumes" the scan
    if signal is not None:
        payload["best_rssi"] = signal.best_rssi
        payload["desk_device_ids"] = [str(i) for i in signal.device_ids]
    start = await append_event(
        ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        type="remote_check_in" if remote else "check_in",
        occurred_at=now,
        source="app",
        day=env.today,
        session_id=session_id,
        event_id=session_id,
        payload=payload,
        actor_user_id=ctx.user_id,
    )
    records = await recompute_days(
        ctx.db, env.user, [env.today], now=now, office=env.office, policies=env.policies
    )
    record = records[env.today]
    return CheckInOut(
        session=session_out(
            _session_info(record, start.id), state="present", reason="check_in", seen=now
        ),
        today=day_out(record),
        integrity=integrity,
        best_rssi=signal.best_rssi if signal else None,
    )


# ---------------------------------------------------------------------------- heartbeat


async def heartbeat(ctx: Ctx, data: HeartbeatIn, key: str | None) -> tuple[int, Any]:
    async def handler() -> tuple[int, Any]:
        return await _heartbeat(ctx, data)

    return await _with_key(ctx, key, "attendance.heartbeat", data.model_dump(mode="json"), handler)


async def _heartbeat(ctx: Ctx, data: HeartbeatIn) -> tuple[int, Any]:
    now = utcnow()
    await lock_user(ctx.db, ctx.org_id, ctx.user_id)
    env = await load_env(ctx, now)
    interval = ctx.settings.heartbeat_interval_seconds
    start = await get_open_session(ctx.db, ctx.user_id)
    if start is None:
        raise conflict("not_checked_in", "You are not checked in")
    rules = policy_for_day(env.policies, start.day).rules
    modules = ctx.org.modules

    closing = await expire_if_needed(ctx.db, start, now, env.policies)
    if closing is not None:
        await recompute_days(
            ctx.db, env.user, [start.day], now=now, office=env.office, policies=env.policies
        )
        body = error_body(
            "session_expired",
            "Your session ended because the app stopped reporting; check in again",
            {"reason": closing.payload.get("reason"), "ended_at": closing.occurred_at.isoformat()},
        )
        return 409, body

    state_before, _, _ = await session_activity(ctx.db, start)
    remote = start.type == "remote_check_in"
    new_state, reason = state_before, "heartbeat"
    distance = accuracy = None
    mock = False
    signal = None
    unreliable = False
    close_reason: str | None = None

    if remote:
        new_state, reason = "present", "remote"
    else:
        geofence = bool(modules.get("geofence") and rules.require_geofence)
        loc = data.location
        inside = True
        if (
            data.device
            and data.device.rooted
            and ctx.org.settings.get("block_rooted_devices", True)
        ):
            inside, close_reason = False, "rooted_device"
        elif loc is None:
            unreliable = geofence  # cannot judge without a fix: keep the last state
        else:
            accuracy = loc.accuracy_m
            problem = location_problem(loc, rules, now)
            if problem is not None and problem.code == "mock_location":
                mock, inside, close_reason = True, False, "mock_location"
            elif problem is not None:
                unreliable = True
            elif env.office is not None:
                distance = distance_to(loc, env.office.latitude, env.office.longitude)
                if geofence and distance > env.office.radius_m:
                    inside, close_reason = False, "outside"
        proximity = bool(modules.get("proximity") and rules.require_proximity)
        if proximity and inside and not unreliable:
            candidates = await load_candidates(ctx.db, ctx.settings, env.user)
            signal = evaluate_for_user(
                candidates, data.beacons, ctx.org.settings, rules, ctx.settings, now
            )
        if not unreliable:
            new_state = decide_state(inside, bool(signal and signal.near), proximity)
            reason = close_reason or presence_reason(inside, signal, proximity)
        else:
            reason = "location_unreliable"

    ctx.db.add(
        PresenceSample(
            id=uuid.uuid4(),
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            session_id=start.id,
            at=now,
            state=new_state,
            reason=reason,
            distance_m=distance,
            best_rssi=signal.best_rssi if signal else None,
            accuracy_m=accuracy,
            mock_location=mock,
            integrity=None,
        )
    )
    changed = False
    if not unreliable and new_state != state_before:
        await append_event(
            ctx.db,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            type="presence_changed",
            occurred_at=now,
            source="app",
            day=start.day,
            session_id=start.id,
            payload={"state": new_state, "reason": reason, "previous": state_before},
        )
        changed = True
    if new_state == "outside":
        await close_session(
            ctx.db,
            start,
            at=now,
            type="auto_check_out",
            source="app",
            reason=close_reason or "outside",
            payload={"distance_m": round(distance, 1)} if distance is not None else None,
        )
        changed = True
        if mock or close_reason == "rooted_device":
            await record_rejection(ctx, close_reason or "mock_location", during="heartbeat")
    await ctx.db.flush()
    records = await recompute_days(
        ctx.db,
        env.user,
        [start.day],
        now=now,
        office=env.office,
        policies=env.policies,
        persist=changed,
    )
    info = _session_info(records[start.day], start.id)
    out = HeartbeatOut(
        checked_in=new_state != "outside",
        state=new_state,
        reason=reason,
        session=session_out(info, state=new_state, reason=reason, seen=now),
        next_heartbeat_seconds=interval,
        best_rssi=signal.best_rssi if signal else None,
    )
    return 200, out.model_dump(mode="json")


# --------------------------------------------------------------------------- check-out


async def check_out(ctx: Ctx, data: CheckOutIn, key: str | None) -> tuple[int, Any]:
    async def handler() -> tuple[int, Any]:
        out = await _check_out(ctx, data)
        return 200, out.model_dump(mode="json")

    return await _with_key(ctx, key, "attendance.check_out", data.model_dump(mode="json"), handler)


async def _check_out(ctx: Ctx, data: CheckOutIn) -> CheckOutOut:
    now = utcnow()
    await lock_user(ctx.db, ctx.org_id, ctx.user_id)
    env = await load_env(ctx, now)
    start = await get_open_session(ctx.db, ctx.user_id)
    if start is None:
        raise conflict("not_checked_in", "You are not checked in")
    if await expire_if_needed(ctx.db, start, now, env.policies) is not None:
        # The session had already lapsed: it is closed at its last evidence (not at "now") and
        # the user is, as they wanted, checked out.
        records = await recompute_days(
            ctx.db, env.user, [start.day], now=now, office=env.office, policies=env.policies
        )
        record = records[start.day]
        return CheckOutOut(
            session=session_out(_session_info(record, start.id)), today=day_out(record)
        )
    payload = None
    if data.location is not None:
        payload = {"location": data.location.model_dump(mode="json", exclude={"mock"})}
    await close_session(
        ctx.db,
        start,
        at=now,
        type="check_out",
        source="app",
        actor_user_id=ctx.user_id,
        payload=payload,
    )
    records = await recompute_days(
        ctx.db, env.user, [start.day], now=now, office=env.office, policies=env.policies
    )
    record = records[start.day]
    return CheckOutOut(
        session=session_out(_session_info(record, start.id), seen=now), today=day_out(record)
    )


# -------------------------------------------------------------------------------- history


async def my_days(ctx: Ctx, start: date | None, end: date | None) -> MeOut:
    env = await load_env(ctx)
    end = end or env.today
    start = start or end - timedelta(days=29)
    if end < start:
        raise bad_request("invalid_range", "end is before start")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise bad_request("range_too_large", f"At most {MAX_RANGE_DAYS} days at a time")
    rows = (
        (
            await ctx.db.execute(
                select(AttendanceDay)
                .where(
                    AttendanceDay.user_id == ctx.user_id,
                    AttendanceDay.day >= start,
                    AttendanceDay.day <= end,
                )
                .order_by(AttendanceDay.day.desc())
            )
        )
        .scalars()
        .all()
    )
    live_days = [r.day for r in rows if {"open", "missing_check_out"} & set(r.flags)]
    live = (
        await recompute_days(
            ctx.db,
            env.user,
            live_days,
            now=env.now,
            office=env.office,
            policies=env.policies,
            persist=False,
        )
        if live_days
        else {}
    )
    days = [day_out(live[r.day]) if r.day in live else DayOut.model_validate(r) for r in rows]
    totals = {
        "worked_minutes": sum(d.worked_minutes for d in days),
        "away_minutes": sum(d.away_minutes for d in days),
        "overtime_minutes": sum(d.overtime_minutes for d in days),
        "days_present": sum(d.status == "present" for d in days),
        "days_half": sum(d.status == "half_day" for d in days),
        "days_absent": sum(d.status == "absent" for d in days),
        "days_on_leave": sum(d.status == "on_leave" for d in days),
        "late_days": sum(d.late_minutes > 0 for d in days),
    }
    return MeOut(start=start, end=end, days=days, totals=totals)
