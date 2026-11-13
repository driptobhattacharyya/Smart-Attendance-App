"""Attendance policy rules (docs/architecture-v5.md section 7) and policy lookup."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AttendancePolicy

SIGNUP_POLICY_EFFECTIVE_FROM = date(2000, 1, 1)


class PolicyRules(BaseModel):
    """Every field has a default so an old stored policy keeps working when fields are added."""

    model_config = ConfigDict(extra="ignore")

    counting: Literal["presence", "span", "span_minus_away"] = "presence"
    require_geofence: bool = True
    require_face: bool = True
    require_proximity: bool = False
    desk_activity: Literal["off", "locked_only", "locked_or_idle"] = "off"
    desk_idle_limit_minutes: int = Field(default=10, ge=1, le=1440)
    heartbeat_grace_minutes: int = Field(default=15, ge=1, le=240)
    late_after_minutes: int = Field(default=10, ge=0, le=600)
    early_leave_before_minutes: int = Field(default=15, ge=0, le=600)
    full_day_minutes: int = Field(default=480, ge=1, le=1440)
    half_day_minutes: int = Field(default=240, ge=0, le=1440)
    overtime_after_minutes: int = Field(default=540, ge=0, le=1440)
    max_session_hours: int = Field(default=14, ge=1, le=48)
    # Trust thresholds (section 8).
    location_max_accuracy_m: int = Field(default=100, ge=5, le=5000)
    location_max_age_seconds: int = Field(default=120, ge=10, le=3600)
    min_rssi: int = Field(default=-85, ge=-127, le=0)
    # Default working hours; an office may override them. "HH:MM".
    work_start: time = time(9, 0)
    work_end: time = time(18, 0)
    # Monday = 0 ... Sunday = 6.
    weekly_off: list[int] = Field(default_factory=lambda: [5, 6])
    holidays: list[date] = Field(default_factory=list)
    # Weekdays on which remote (WFH) check-in needs no approved request.
    wfh_weekdays: list[int] = Field(default_factory=list)

    @field_validator("weekly_off", "wfh_weekdays")
    @classmethod
    def _weekdays(cls, value: list[int]) -> list[int]:
        if any(not 0 <= d <= 6 for d in value):
            raise ValueError("weekdays are 0 (Monday) to 6 (Sunday)")
        return sorted(set(value))

    @field_validator("holidays")
    @classmethod
    def _holidays(cls, value: list[date]) -> list[date]:
        return sorted(set(value))

    @model_validator(mode="after")
    def _thresholds(self):
        if self.half_day_minutes > self.full_day_minutes:
            raise ValueError("half_day_minutes cannot exceed full_day_minutes")
        return self

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


DEFAULT_RULES = PolicyRules()


class EffectivePolicy(BaseModel):
    id: uuid.UUID | None
    effective_from: date | None
    rules: PolicyRules


async def policies_for_org(session: AsyncSession) -> list[AttendancePolicy]:
    """All versions, newest first (RLS limits this to the caller's org)."""
    return list(
        (
            await session.execute(
                select(AttendancePolicy).order_by(
                    AttendancePolicy.effective_from.desc(), AttendancePolicy.created_at.desc()
                )
            )
        ).scalars()
    )


def policy_for_day(policies: list[AttendancePolicy], day: date) -> EffectivePolicy:
    """The latest version with ``effective_from <= day`` (``policies`` newest first); the built-in
    defaults when the org has none yet."""
    for policy in policies:
        if policy.effective_from <= day:
            return EffectivePolicy(
                id=policy.id,
                effective_from=policy.effective_from,
                rules=PolicyRules.model_validate(policy.rules),
            )
    return EffectivePolicy(id=None, effective_from=None, rules=DEFAULT_RULES)
