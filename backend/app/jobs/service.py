"""Scheduled jobs (EventBridge -> ``/api/v1/internal/jobs/*``).

Each job lists the organizations with the system (BYPASSRLS) role, then does its work per
organization inside a tenant transaction (``SET LOCAL app.org_id``), so the same RLS that guards
requests guards the jobs. The retention job is the one exception that needs the system role for
its writes: it deletes samples and is the only caller allowed to touch ``attendance_events``
(payload only, via ``app.retention_job = on``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select, text

from ..attendance.lifecycle import expire_if_needed
from ..attendance.models import FaceChallengeUsed, PresenceSample
from ..attendance.rules import policies_for_org
from ..attendance.store import (
    get_open_session,
    lock_user,
    open_session_query,
    recompute_days,
)
from ..core.config import Settings
from ..core.db import Database
from ..core.idempotency import purge_expired
from ..core.security import utcnow
from ..people.models import User
from ..places.models import Office
from ..tenancy.models import Organization

logger = logging.getLogger("attendify.jobs")
MAX_RECOMPUTE_DAYS = 93


async def _org_ids(database: Database, only: uuid.UUID | None = None) -> list[uuid.UUID]:
    async with database.system_session() as s:
        stmt = select(Organization.id).where(Organization.status == "active")
        if only:
            stmt = stmt.where(Organization.id == only)
        return list((await s.execute(stmt.order_by(Organization.created_at))).scalars())


async def close_stale_sessions(database: Database) -> dict[str, Any]:
    """Auto check-out every open session whose heartbeats stopped (grace) or that outlived the
    maximum session length."""
    now = utcnow()
    closed = failed = 0
    for org_id in await _org_ids(database):
        async with database.tenant_session(org_id) as db:
            stmt, s = open_session_query()
            candidates = list((await db.execute(stmt)).scalars())
            policies = await policies_for_org(db)
            for candidate in candidates:
                try:
                    async with db.begin_nested():
                        await lock_user(db, org_id, candidate.user_id)
                        start = await get_open_session(db, candidate.user_id)
                        if start is None or start.id != candidate.id:
                            continue  # closed meanwhile
                        if await expire_if_needed(db, start, now, policies) is None:
                            continue
                        user = (
                            await db.execute(select(User).where(User.id == start.user_id))
                        ).scalar_one()
                        await recompute_days(db, user, [start.day], now=now, policies=policies)
                        closed += 1
                except Exception:
                    failed += 1
                    logger.exception("closing session %s failed", candidate.id)
    return {"closed": closed, "failed": failed}


async def retention(database: Database, settings: Settings) -> dict[str, Any]:
    """Section 9: drop presence samples and coordinates older than the org's retention window.
    Daily totals (``attendance_days``) and the event skeleton are kept."""
    now = utcnow()
    totals = {
        "samples_deleted": 0,
        "events_stripped": 0,
        "challenges_deleted": 0,
        "keys_deleted": 0,
    }
    for org_id in await _org_ids(database):
        async with database.system_session() as s:
            org = (
                await s.execute(select(Organization).where(Organization.id == org_id))
            ).scalar_one()
            days = int(
                org.settings.get("presence_retention_days", settings.presence_retention_days)
            )
            cutoff = now - timedelta(days=days)
            await s.execute(text(f"SET LOCAL app.org_id = '{org_id}'"))
            await s.execute(text("SET LOCAL app.retention_job = 'on'"))
            samples = await s.execute(
                delete(PresenceSample).where(
                    PresenceSample.org_id == org_id, PresenceSample.at < cutoff
                )
            )
            stripped = await s.execute(
                text(
                    """UPDATE attendance_events
                       SET payload = ((payload #- '{location,lat}') #- '{location,lon}')
                                     || '{"coords_stripped": true}'::jsonb
                       WHERE org_id = :org AND occurred_at < :cutoff
                         AND payload #> '{location,lat}' IS NOT NULL"""
                ),
                {"org": org_id, "cutoff": cutoff},
            )
            challenges = await s.execute(
                delete(FaceChallengeUsed).where(
                    FaceChallengeUsed.org_id == org_id,
                    FaceChallengeUsed.expires_at < now - timedelta(days=1),
                )
            )
            totals["samples_deleted"] += samples.rowcount or 0
            totals["events_stripped"] += stripped.rowcount or 0
            totals["challenges_deleted"] += challenges.rowcount or 0
            totals["keys_deleted"] += await purge_expired(s)
    return totals


async def recompute(
    database: Database,
    *,
    start: date,
    end: date,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Rebuild ``attendance_days`` from the event log for a date range (after a policy or office
    change, or to materialise absences). Active users get a row for every day that is over."""
    if end < start or (end - start).days + 1 > MAX_RECOMPUTE_DAYS:
        raise ValueError(f"range must be 1..{MAX_RECOMPUTE_DAYS} days")
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    now = utcnow()
    orgs = users_done = 0
    for oid in await _org_ids(database, org_id):
        async with database.tenant_session(oid) as db:
            policies = await policies_for_org(db)
            offices = {o.id: o for o in (await db.execute(select(Office))).scalars()}
            stmt = select(User)
            if user_id:
                stmt = stmt.where(User.id == user_id)
            for user in (await db.execute(stmt)).scalars():
                if user.status == "invited":
                    continue
                office = offices.get(user.office_id) if user.office_id else None
                await recompute_days(db, user, days, now=now, office=office, policies=policies)
                users_done += 1
        orgs += 1
    return {"orgs": orgs, "users": users_done, "days": len(days)}
