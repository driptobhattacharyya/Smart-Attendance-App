"""Organization settings, modules and plan limits (tenant-scoped; signup lives in signup.py)."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import Ctx
from ..core.errors import AppError, bad_request
from ..people.models import User
from .models import Organization

DEFAULT_MODULES: dict[str, bool] = {
    "geofence": True,
    "face": True,
    "proximity": False,
    "desk_app": False,
    "wfh": False,
    "regularization": True,
    "leave": True,
}

# Settings an org owner may change, with a validator each. Everything else (desk_uuid, plan) is
# platform-controlled.
EDITABLE_SETTINGS: dict[str, Any] = {
    "block_rooted_devices": lambda v: isinstance(v, bool),
    "presence_retention_days": lambda v: (
        isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 3650
    ),
    "desk_activity_monitoring": lambda v: isinstance(v, bool),
}


def default_settings(max_employees: int, retention_days: int) -> dict[str, Any]:
    return {
        "desk_uuid": str(uuid.uuid4()),
        "plan": {"name": "trial", "max_employees": max_employees},
        "block_rooted_devices": True,
        "presence_retention_days": retention_days,
        "desk_activity_monitoring": False,
    }


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "org")[:40].strip("-") or "org"


async def get_org(session: AsyncSession) -> Organization:
    return (await session.execute(select(Organization))).scalar_one()


def org_snapshot(org: Organization) -> dict[str, Any]:
    return {
        "name": org.name,
        "modules": org.modules,
        "settings": {k: v for k, v in org.settings.items() if k != "desk_uuid"},
        "consent_version": org.consent_version,
    }


async def update_org(ctx: Ctx, *, name, modules, settings, bump_consent_version) -> Organization:
    org = await get_org(ctx.db)
    before = org_snapshot(org)
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name.strip()
    if modules:
        unknown = set(modules) - set(DEFAULT_MODULES)
        if unknown:
            raise bad_request("unknown_module", "Unknown module", modules=sorted(unknown))
        values["modules"] = {**org.modules, **modules}
    if settings:
        bad = [
            k
            for k, v in settings.items()
            if k not in EDITABLE_SETTINGS or not EDITABLE_SETTINGS[k](v)
        ]
        if bad:
            raise bad_request("invalid_setting", "Unknown or invalid setting", settings=sorted(bad))
        values["settings"] = {**org.settings, **settings}
    if bump_consent_version:
        values["consent_version"] = org.consent_version + 1
    if values:
        await ctx.db.execute(update(Organization).where(Organization.id == org.id).values(**values))
        await ctx.db.refresh(org)
        await ctx.audit("org.update", "organization", org.id, before, org_snapshot(org))
    return org


async def count_seats(session: AsyncSession) -> int:
    """Users that occupy a seat: everyone not disabled (invited users count)."""
    return (
        await session.execute(
            select(func.count()).select_from(User).where(User.status != "disabled")
        )
    ).scalar_one()


async def assert_seat_available(session: AsyncSession, org: Organization) -> None:
    # Serialize concurrent user creation so two requests cannot both take the last seat.
    await session.execute(
        select(Organization.id).where(Organization.id == org.id).with_for_update()
    )
    limit = (org.settings.get("plan") or {}).get("max_employees")
    if limit is not None and await count_seats(session) >= limit:
        raise AppError(
            403,
            "plan_limit_reached",
            "Your plan's employee limit has been reached",
            {"max_employees": limit},
        )
