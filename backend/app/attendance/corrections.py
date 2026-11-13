"""Direct corrections by administrators (``attendance.correct``): add an interval, void an event.

Both only append events; history is never edited. A reason is mandatory, the actor is recorded on
the event, and every correction is audited. Nobody corrects their own record.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from ..auth.deps import Ctx
from ..core.errors import conflict, forbidden, not_found
from ..core.security import utcnow
from ..people.models import User
from ..places.models import Office
from ..rbac.permissions import ATTENDANCE_CORRECT
from .models import AttendanceEvent
from .schemas import EventOut, IntervalCorrectionIn, VoidEventIn
from .store import append_event, get_user, local_date, lock_user, office_info, recompute_days


async def _target(ctx: Ctx, user_id: uuid.UUID) -> User:
    ctx.require(ATTENDANCE_CORRECT)
    if user_id == ctx.user_id:
        raise forbidden("You cannot correct your own attendance", "cannot_correct_own")
    user = (await ctx.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise not_found("Person")
    return user


async def _office(ctx: Ctx, user: User) -> Office | None:
    if not user.office_id:
        return None
    return (
        await ctx.db.execute(select(Office).where(Office.id == user.office_id))
    ).scalar_one_or_none()


async def add_interval(ctx: Ctx, data: IntervalCorrectionIn) -> EventOut:
    user = await _target(ctx, data.user_id)
    await lock_user(ctx.db, ctx.org_id, user.id)
    office = await _office(ctx, user)
    day = local_date(data.start, office_info(office).timezone)
    event = await append_event(
        ctx.db,
        org_id=ctx.org_id,
        user_id=user.id,
        type="interval_added",
        occurred_at=data.start,
        source="admin",
        day=day,
        actor_user_id=ctx.user_id,
        payload={
            "kind": data.kind,
            "start": data.start.isoformat(),
            "end": data.end.isoformat(),
            "reason": data.reason,
        },
    )
    await recompute_days(ctx.db, user, [day], office=office)
    await ctx.audit(
        "attendance.interval_added",
        "user",
        user.id,
        after={"event_id": event.id, "kind": data.kind, "day": day, "reason": data.reason},
    )
    return EventOut.model_validate(event)


async def void_event(ctx: Ctx, data: VoidEventIn) -> EventOut:
    ctx.require(ATTENDANCE_CORRECT)
    target = (
        await ctx.db.execute(select(AttendanceEvent).where(AttendanceEvent.id == data.event_id))
    ).scalar_one_or_none()
    if target is None:
        raise not_found("Event")
    user = await _target(ctx, target.user_id)
    if target.type == "event_voided":
        raise conflict("not_voidable", "A void marker cannot be voided")
    await lock_user(ctx.db, ctx.org_id, user.id)
    other = aliased(AttendanceEvent)
    already = (
        await ctx.db.execute(select(exists().where(other.voids_event_id == target.id)))
    ).scalar_one()
    if already:
        raise conflict("already_voided", "That event is already voided")
    office = await _office(ctx, user)
    event = await append_event(
        ctx.db,
        org_id=ctx.org_id,
        user_id=user.id,
        type="event_voided",
        occurred_at=utcnow(),
        source="admin",
        day=target.day,
        voids_event_id=target.id,
        actor_user_id=ctx.user_id,
        payload={"reason": data.reason, "voided_type": target.type},
    )
    await recompute_days(ctx.db, user, [target.day], office=office)
    await ctx.audit(
        "attendance.event_voided",
        "user",
        user.id,
        before={"event_id": target.id, "type": target.type, "day": target.day},
        after={"void_event_id": event.id, "reason": data.reason},
    )
    out = EventOut.model_validate(event)
    return out


__all__ = ["add_interval", "void_event", "get_user"]
