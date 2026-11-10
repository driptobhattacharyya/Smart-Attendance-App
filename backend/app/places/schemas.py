from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError("Unknown IANA timezone") from exc
    return value


class OfficeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=150, ge=10, le=5000)
    timezone: str = "UTC"
    address: str | None = Field(default=None, max_length=300)
    work_start: time | None = None
    work_end: time | None = None
    active: bool = True

    _tz = field_validator("timezone")(_check_timezone)


class OfficeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int | None = Field(default=None, ge=10, le=5000)
    timezone: str | None = None
    address: str | None = Field(default=None, max_length=300)
    work_start: time | None = None
    work_end: time | None = None
    active: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        return _check_timezone(value) if value is not None else value


class OfficeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    latitude: float
    longitude: float
    radius_m: int
    timezone: str
    address: str | None
    work_start: time | None
    work_end: time | None
    active: bool


class DeskDeviceCreate(BaseModel):
    kind: Literal["tag", "desk_app"]
    name: str = Field(min_length=1, max_length=120)
    office_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    # Tags only: the fixed iBeacon identity printed on the tag.
    ibeacon_uuid: uuid.UUID | None = None
    major: int | None = Field(default=None, ge=0, le=65535)
    minor: int | None = Field(default=None, ge=0, le=65535)

    @model_validator(mode="after")
    def _identity(self):
        if self.kind == "tag":
            if self.ibeacon_uuid is None or self.major is None or self.minor is None:
                raise ValueError("Tags need ibeacon_uuid, major and minor")
        elif any(v is not None for v in (self.ibeacon_uuid, self.major, self.minor)):
            raise ValueError("Desk apps get their rotating identity at pairing")
        return self


class DeskDeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    office_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    active: bool | None = None
    # Tags only.
    ibeacon_uuid: uuid.UUID | None = None
    major: int | None = Field(default=None, ge=0, le=65535)
    minor: int | None = Field(default=None, ge=0, le=65535)


class DeskDeviceOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    office_id: uuid.UUID | None
    assigned_user_id: uuid.UUID | None
    active: bool
    # Tags: the fixed identity. Desk apps: the org desk UUID, major/minor always null (rotating).
    ibeacon_uuid: uuid.UUID | None
    major: int | None
    minor: int | None
    paired: bool
    paired_at: datetime | None
    last_seen_at: datetime | None
    device: dict[str, Any]


class PairingCodeOut(BaseModel):
    code: str
    expires_at: datetime


class DeskPairIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)
    hostname: str | None = Field(default=None, max_length=200)
    os: str | None = Field(default=None, max_length=100)
    agent_version: str | None = Field(default=None, max_length=50)


class DeskBeaconBrief(BaseModel):
    id: uuid.UUID
    name: str
    uuid: uuid.UUID
    window_seconds: int


class DeskEmployee(BaseModel):
    id: uuid.UUID
    employee_code: str
    name: str


class DeskPairOut(BaseModel):
    device_token: str
    # base64 of the 32-byte secret. Returned exactly once, here.
    secret: str
    beacon: DeskBeaconBrief
    employee: DeskEmployee | None
    heartbeat_interval_seconds: int
    server_time: datetime


class DeskHeartbeatIn(BaseModel):
    locked: bool = False
    idle_seconds: int = Field(default=0, ge=0, le=10**7)
    advertising: bool = False
    agent_version: str | None = Field(default=None, max_length=50)


class DeskHeartbeatOut(BaseModel):
    server_time: datetime
    heartbeat_interval_seconds: int
    paired: bool = True
