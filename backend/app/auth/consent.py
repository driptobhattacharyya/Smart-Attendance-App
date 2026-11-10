"""Per-organization, versioned consent (text adapted from the v3 contract)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select, update

from ..core.config import Settings
from ..core.errors import conflict
from ..tenancy.models import Organization
from .deps import Ctx, consent_required_for
from .models import Consent

TITLE = "How your employer uses Attendify to record attendance"

_OPENCV_NOTE = (
    "The photo is used to calculate those numbers on our server and is not stored. "
    "Only the numbers are kept."
)
_AZURE_NOTE = (
    "Your photo is sent to Microsoft Azure's face service for processing, which also keeps the "
    "face data it extracts so it can recognise you later. Attendify itself does not store the "
    "photo; it keeps only the numbers and an identifier."
)

_DESK_APP_SECTION = """## The Attendify Desk app

Your company also uses the Attendify Desk app on office computers, which can stand in for the \
desk beacon. The app on your desk computer shares only:

- whether the computer is **locked or unlocked**,
- **how many seconds** it is since the last keyboard or mouse input (not what was typed),
- whether it is **broadcasting** its Bluetooth signal,
- the **app version**, and the computer's **name and operating system**.

It never records keystrokes, screenshots, window titles or which apps you use. If the computer \
is locked or has been idle for a long time, the time is counted as away from your desk.

"""


@lru_cache
def _template() -> str:
    return Path(__file__).with_name("consent.md").read_text("utf-8")


def render_consent(settings: Settings, org: Organization) -> str:
    note = _AZURE_NOTE if settings.face_provider == "azure" else _OPENCV_NOTE
    retention = org.settings.get("presence_retention_days", settings.presence_retention_days)
    desk = bool(org.modules.get("desk_app"))
    return (
        _template()
        .replace("{desk_app_section}", _DESK_APP_SECTION if desk else "")
        .replace("{face_processing}", note)
        .replace("{retention_days}", str(retention))
    )


async def consent_text(ctx: Ctx) -> dict:
    return {
        "version": ctx.org.consent_version,
        "title": TITLE,
        "body_markdown": render_consent(ctx.settings, ctx.org),
        "accepted": not await consent_required_for(ctx.db, ctx.org, ctx.user_id),
    }


async def accept_consent(ctx: Ctx, version: int) -> None:
    if version != ctx.org.consent_version:
        raise conflict(
            "consent_version_mismatch",
            "That is not the current consent version",
            current_version=ctx.org.consent_version,
        )
    if not await consent_required_for(ctx.db, ctx.org, ctx.user_id):
        return  # already accepted: idempotent
    ctx.db.add(Consent(org_id=ctx.org_id, user_id=ctx.user_id, version=version, ip=ctx.ip))
    await ctx.db.flush()
    await ctx.audit("consent.accept", "user", ctx.user_id, after={"version": version})


async def withdraw_consent(ctx: Ctx) -> None:
    result = await ctx.db.execute(
        update(Consent)
        .where(Consent.user_id == ctx.user_id, Consent.withdrawn_at.is_(None))
        .values(withdrawn_at=func.now())
    )
    if result.rowcount:
        await ctx.audit("consent.withdraw", "user", ctx.user_id)


async def has_active_consent(ctx: Ctx) -> bool:
    return (
        await ctx.db.execute(
            select(Consent.id).where(Consent.user_id == ctx.user_id, Consent.withdrawn_at.is_(None))
        )
    ).first() is not None
