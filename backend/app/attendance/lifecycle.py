"""Closing sessions on the system's behalf: expiry, consent withdrawal, disabled accounts, jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import utcnow
from .models import AttendanceEvent
from .rules import policies_for_org, policy_for_day
from .store import (
    close_session,
    get_open_session,
    get_user,
    lock_user,
    recompute_days,
    session_activity,
)


async def expire_if_needed(
    db: AsyncSession, start: AttendanceEvent, now: datetime, policies=None
) -> AttendanceEvent | None:
    """Auto check-out an open session that has outlived its evidence or the maximum session
    length. Returns the closing event, or None when the session is still valid.

    * no heartbeat for longer than the grace window -> closed at the last evidence (``stale``);
    * running longer than ``max_session_hours`` -> closed at that limit (``max_session``).
    The caller holds the user lock and recomputes the day.
    """
    policies = policies if policies is not None else await policies_for_org(db)
    rules = policy_for_day(list(policies), start.day).rules
    _, _, evidence = await session_activity(db, start)
    grace = timedelta(minutes=rules.heartbeat_grace_minutes)
    cap = start.occurred_at + timedelta(hours=rules.max_session_hours)
    if now - evidence > grace:
        at, why = (cap, "max_session") if evidence >= cap else (evidence, "stale")
    elif now >= cap:
        at, why = cap, "max_session"
    else:
        return None
    return await close_session(db, start, at=at, type="auto_check_out", source="system", reason=why)


async def end_open_session(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    reason: str,
    now: datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> bool:
    """Check a user out (``check_out``, source ``system``) if they are checked in. Used when
    consent is withdrawn or the account is disabled. Returns whether a session was closed."""
    now = now or utcnow()
    await lock_user(db, org_id, user_id)
    start = await get_open_session(db, user_id)
    if start is None:
        return False
    await close_session(
        db,
        start,
        at=now,
        type="check_out",
        source="system",
        reason=reason,
        actor_user_id=actor_user_id,
    )
    await recompute_days(db, await get_user(db, user_id), [start.day], now=now)
    return True
