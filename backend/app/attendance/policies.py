"""Versioned attendance policies."""

from __future__ import annotations

import uuid
from datetime import timedelta

from ..auth.deps import Ctx
from ..core.errors import bad_request
from ..core.security import utcnow
from .models import AttendancePolicy
from .rules import policies_for_org, policy_for_day
from .schemas import PolicyCreate, PolicyOut
from .service import load_env
from .store import local_date, office_info


def _out(p: AttendancePolicy) -> PolicyOut:
    from .rules import PolicyRules

    return PolicyOut(
        id=p.id,
        effective_from=p.effective_from,
        rules=PolicyRules.model_validate(p.rules),
        created_at=p.created_at,
        created_by=p.created_by,
    )


async def list_policies(ctx: Ctx) -> list[PolicyOut]:
    return [_out(p) for p in await policies_for_org(ctx.db)]


async def effective_policy(ctx: Ctx) -> PolicyOut:
    env = await load_env(ctx)
    eff = policy_for_day(env.policies, env.today)
    return PolicyOut(id=eff.id, effective_from=eff.effective_from, rules=eff.rules)


async def create_policy(ctx: Ctx, data: PolicyCreate) -> PolicyOut:
    # Policies are versions that apply from a date on. Past days keep the rules they were
    # calculated with; back-dating by more than a day would silently change history.
    earliest = utcnow().date() - timedelta(days=1)
    if data.effective_from < earliest:
        raise bad_request(
            "policy_in_past", "A policy cannot take effect in the past", earliest=str(earliest)
        )
    row = AttendancePolicy(
        id=uuid.uuid4(),
        org_id=ctx.org_id,
        effective_from=data.effective_from,
        rules=data.rules.to_json(),
        created_by=ctx.user_id,
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.db.refresh(row)
    await ctx.audit(
        "policy.create",
        "attendance_policy",
        row.id,
        after={"effective_from": row.effective_from, "rules": row.rules},
    )
    return _out(row)


__all__ = ["create_policy", "effective_policy", "list_policies", "local_date", "office_info"]
