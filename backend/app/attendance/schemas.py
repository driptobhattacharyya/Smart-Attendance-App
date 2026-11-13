from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .rules import PolicyRules

# ------------------------------------------------------------------------------ evidence


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100_000)
    # Android isMock / isFromMockProvider. The server never trusts a client "ok" flag, but it does
    # act on a client "this is fake" flag.
    mock: bool = False
    captured_at: datetime | None = None


class DeviceIn(BaseModel):
    platform: str = Field(default="unknown", max_length=20)
    app_version: str | None = Field(default=None, max_length=40)
    rooted: bool = False
    integrity_token: str | None = Field(default=None, max_length=20_000)
    # The nonce from GET /attendance/integrity-nonce that the token was requested with.
    integrity_nonce: str | None = Field(default=None, max_length=200)


class BeaconReading(BaseModel):
    uuid: uuid.UUID
    major: int = Field(ge=0, le=65535)
    minor: int = Field(ge=0, le=65535)
    rssi: int = Field(ge=-127, le=20)


class CheckInIn(BaseModel):
    # "onsite" is the mobile app's spelling; "office" is accepted as an alias. Both are stored
    # and reported as "office".
    mode: Literal["onsite", "office", "remote"] = "onsite"
    location: LocationIn | None = None
    device: DeviceIn | None = None
    beacons: list[BeaconReading] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _alias(self):
        if self.mode == "onsite":
            self.mode = "office"
        return self


class HeartbeatIn(BaseModel):
    location: LocationIn | None = None
    device: DeviceIn | None = None
    beacons: list[BeaconReading] = Field(default_factory=list, max_length=64)


class CheckOutIn(BaseModel):
    location: LocationIn | None = None


# ------------------------------------------------------------------------------ outputs


class SessionOut(BaseModel):
    id: uuid.UUID
    mode: str
    started_at: datetime
    ended_at: datetime | None
    open: bool
    state: str | None = None
    reason: str | None = None
    last_seen_at: datetime | None = None
    worked_minutes: int
    away_minutes: int


class DayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    status: str
    first_in: datetime | None
    last_out: datetime | None
    worked_minutes: int
    away_minutes: int
    break_minutes: int
    late_minutes: int
    early_leave_minutes: int
    overtime_minutes: int
    flags: list[str]
    sessions: list[dict[str, Any]]


class StatusOut(BaseModel):
    checked_in: bool
    session: SessionOut | None
    today: DayOut | None
    face_verified_until: datetime | None
    face_enrolled: bool
    presence: str | None = None
    today_minutes: int = 0
    heartbeat_interval_seconds: int
    wfh_allowed: bool
    requirements: dict[str, bool]


class CheckInOut(BaseModel):
    session: SessionOut
    today: DayOut
    integrity: str
    best_rssi: int | None = None


class HeartbeatOut(BaseModel):
    checked_in: bool
    state: str
    reason: str | None
    session: SessionOut | None
    next_heartbeat_seconds: int
    best_rssi: int | None = None


class CheckOutOut(BaseModel):
    session: SessionOut
    today: DayOut


class MeOut(BaseModel):
    start: date
    end: date
    days: list[DayOut]
    totals: dict[str, int]


# ------------------------------------------------------------------------------ policies


class PolicyCreate(BaseModel):
    effective_from: date
    rules: PolicyRules


class PolicyOut(BaseModel):
    id: uuid.UUID | None
    effective_from: date | None
    rules: PolicyRules
    created_at: datetime | None = None
    created_by: uuid.UUID | None = None


# ------------------------------------------------------------------------------ requests


class RequestCreate(BaseModel):
    kind: Literal["correction", "leave", "wfh"]
    start_date: date
    end_date: date | None = None
    entry_time: time | None = None
    exit_time: time | None = None
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("reason is too short")
        return value

    @model_validator(mode="after")
    def _shape(self):
        if self.end_date is None:
            self.end_date = self.start_date
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if self.kind == "correction":
            if self.entry_time is None or self.exit_time is None:
                raise ValueError("a correction needs entry_time and exit_time")
            if self.end_date != self.start_date:
                raise ValueError("a correction covers a single day")
        elif self.entry_time is not None or self.exit_time is not None:
            raise ValueError("entry_time/exit_time are only for corrections")
        return self


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    kind: str
    start_date: date
    end_date: date
    entry_time: time | None
    exit_time: time | None
    reason: str
    status: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    comment: str | None
    created_at: datetime


class DecisionIn(BaseModel):
    action: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1000)


# ------------------------------------------------------------------------------ corrections


class IntervalCorrectionIn(BaseModel):
    user_id: uuid.UUID
    start: datetime
    end: datetime
    kind: Literal["work", "break"] = "work"
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def _order(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end need a time zone offset")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if (self.end - self.start).total_seconds() > 24 * 3600:
            raise ValueError("an interval can be at most 24 hours")
        return self


class VoidEventIn(BaseModel):
    event_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=1000)


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    type: str
    source: str
    occurred_at: datetime
    recorded_at: datetime
    session_id: uuid.UUID | None
    day: date
    payload: dict[str, Any]
    voids_event_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    request_id: uuid.UUID | None
    voided: bool = False
