"""Right of access: everything Attendify holds about the caller, as JSON."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..auth.deps import Ctx
from ..auth.models import Consent
from ..people.models import User
from .face_store import get_enrollment
from .models import AttendanceDay, AttendanceEvent, PresenceSample, Request
from .schemas import DayOut, EventOut, RequestOut

EVENT_LIMIT = 50_000


async def export_me(ctx: Ctx) -> dict[str, Any]:
    user = (await ctx.db.execute(select(User).where(User.id == ctx.user_id))).scalar_one()
    days = (
        (
            await ctx.db.execute(
                select(AttendanceDay)
                .where(AttendanceDay.user_id == ctx.user_id)
                .order_by(AttendanceDay.day)
            )
        )
        .scalars()
        .all()
    )
    events = (
        (
            await ctx.db.execute(
                select(AttendanceEvent)
                .where(AttendanceEvent.user_id == ctx.user_id)
                .order_by(AttendanceEvent.occurred_at)
                .limit(EVENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    samples = (
        (
            await ctx.db.execute(
                select(PresenceSample)
                .where(PresenceSample.user_id == ctx.user_id)
                .order_by(PresenceSample.at)
                .limit(EVENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    requests = (
        (
            await ctx.db.execute(
                select(Request).where(Request.user_id == ctx.user_id).order_by(Request.created_at)
            )
        )
        .scalars()
        .all()
    )
    consents = (
        (await ctx.db.execute(select(Consent).where(Consent.user_id == ctx.user_id)))
        .scalars()
        .all()
    )
    enrollment = await get_enrollment(ctx.db, ctx.user_id)
    await ctx.audit("data.export", "user", ctx.user_id)
    return {
        "profile": {
            c: getattr(user, c)
            for c in (
                "id employee_code email name phone status role_key manager_id department_id "
                "office_id created_at"
            ).split()
        },
        # Embedding values are never exported: only the fact (and date) of enrolment.
        "face": {
            "enrolled": enrollment is not None,
            "enrolled_at": enrollment.enrolled_at if enrollment else None,
        },
        "consents": [
            {"version": c.version, "accepted_at": c.accepted_at, "withdrawn_at": c.withdrawn_at}
            for c in consents
        ],
        "days": [DayOut.model_validate(d) for d in days],
        "events": [EventOut.model_validate(e) for e in events],
        "presence_samples": [
            {
                "session_id": s.session_id,
                "at": s.at,
                "state": s.state,
                "reason": s.reason,
                "distance_m": s.distance_m,
                "best_rssi": s.best_rssi,
            }
            for s in samples
        ],
        "requests": [RequestOut.model_validate(r) for r in requests],
    }
