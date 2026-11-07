import uuid as uuidlib
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .security import Role

# ---------- Offices ----------


def _validate_tz(cls, v: str | None) -> str | None:
    if v is None:
        return v
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone {v!r}") from exc
    return v


class OfficeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius: float = Field(gt=0, le=10_000, description="Geofence radius in metres")
    timezone: str = "UTC"
    address: str | None = None

    _tz = field_validator("timezone")(_validate_tz)


class OfficeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius: float | None = Field(default=None, gt=0, le=10_000)
    timezone: str | None = None
    address: str | None = None

    _tz = field_validator("timezone")(_validate_tz)


class Office(OfficeIn):
    id: str


# ---------- Users / auth ----------


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    office_id: str
    bluetooth_device: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    employee_id: str = Field(min_length=1, description="Employee ID or email")
    password: str


class UserOut(BaseModel):
    employee_id: str
    name: str
    email: str
    phone: str | None = None
    role: Role
    team_id: str | None = None
    office_id: str | None = None
    bluetooth_device_id: str | None = None
    face_enrolled: bool = False
    active: bool = True


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    office: Office | None = None
    # Flat fields kept for the mobile app's convenience.
    employee_id: str
    bluetooth_device_id: str | None = None
    face_enrolled: bool = False


class UserUpdate(BaseModel):
    """Admin-only changes to a user."""

    name: str | None = None
    role: Role | None = None
    team_id: str | None = None
    office_id: str | None = None
    bluetooth_device_id: str | None = None
    active: bool | None = None


class AdminCreateUser(RegisterIn):
    role: Role = Role.employee
    team_id: str | None = None


# ---------- Beacons ----------

MAX_BEACON_ID = 65535


def normalize_uuid(value: str) -> str:
    """iBeacon proximity UUID in canonical lowercase dashed form."""
    try:
        return str(uuidlib.UUID(value.strip()))
    except (ValueError, AttributeError) as exc:
        raise ValueError("uuid must be a valid UUID") from exc


class BeaconIn(BaseModel):
    office_id: str
    name: str = Field(min_length=1, max_length=120)
    uuid: str
    major: int | None = Field(default=None, ge=0, le=MAX_BEACON_ID, description="null = any")
    minor: int | None = Field(default=None, ge=0, le=MAX_BEACON_ID, description="null = any")
    assigned_employee_id: str | None = Field(
        default=None, description="null = shared zone beacon for the whole office"
    )
    active: bool = True

    @field_validator("uuid")
    @classmethod
    def _uuid(cls, v: str) -> str:
        return normalize_uuid(v)


class BeaconUpdate(BaseModel):
    office_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    uuid: str | None = None
    major: int | None = Field(default=None, ge=0, le=MAX_BEACON_ID)
    minor: int | None = Field(default=None, ge=0, le=MAX_BEACON_ID)
    assigned_employee_id: str | None = None
    active: bool | None = None

    @field_validator("uuid")
    @classmethod
    def _uuid(cls, v: str | None) -> str | None:
        return None if v is None else normalize_uuid(v)

    @model_validator(mode="after")
    def _no_null_required(self) -> "BeaconUpdate":
        # major/minor/assigned_employee_id may be set to null; the others may not.
        for field in ("office_id", "name", "uuid", "active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class BeaconOut(BaseModel):
    id: str
    office_id: str
    name: str
    uuid: str
    major: int | None
    minor: int | None
    assigned_employee_id: str | None
    active: bool


class BeaconReading(BaseModel):
    """What the phone heard: one iBeacon advertisement."""

    uuid: str = Field(max_length=64)
    major: int | None = Field(default=None, ge=0, le=MAX_BEACON_ID)
    minor: int | None = Field(default=None, ge=0, le=MAX_BEACON_ID)
    rssi: int = Field(ge=-200, le=20)


# ---------- Face ----------


class FaceEnrollOut(BaseModel):
    enrolled: bool
    face_id: str


class FaceVerifyOut(BaseModel):
    verified: bool
    score: float
    valid_until: datetime | None = None
    message: str


# ---------- Attendance ----------

PresenceState = Literal["present", "away", "outside"]


class CheckInIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float | None = Field(default=None, ge=0, description="GPS accuracy in metres")
    beacons: list[BeaconReading] = Field(default_factory=list, max_length=64)


class HeartbeatIn(CheckInIn):
    pass


class CheckOutIn(BaseModel):
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class SessionOut(BaseModel):
    id: str
    date: str
    started_at: datetime
    ended_at: datetime | None
    source: Literal["app", "request"]
    minutes: int
    present_minutes: int = 0
    away_minutes: int = 0
    state: PresenceState | None = None


class StatusOut(BaseModel):
    checked_in: bool
    session: SessionOut | None = None
    face_verified_until: datetime | None = None
    today_minutes: int
    presence: PresenceState | None = None
    heartbeat_interval_seconds: int


class HeartbeatOut(BaseModel):
    state: PresenceState
    checked_in: bool
    session: SessionOut | None = None
    next_heartbeat_seconds: int


class DaySummary(BaseModel):
    date: str
    total_minutes: int
    sessions: list[SessionOut]
    on_leave: bool = False


class AttendanceReport(BaseModel):
    employee_id: str
    name: str
    start: date
    end: date
    total_minutes: int
    days: list[DaySummary]


# ---------- Requests ----------


class RequestIn(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": "Forgot to check in",
                "start_date": "2026-11-03",
                "end_date": "2026-11-03",
                "entry_time": "09:30",
                "exit_time": "18:00",
            }
        }
    )

    reason: str = Field(min_length=3, max_length=1000)
    start_date: date
    end_date: date
    entry_time: time | None = None
    exit_time: time | None = None

    @model_validator(mode="after")
    def _check(self) -> "RequestIn":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if (self.end_date - self.start_date).days > 60:
            raise ValueError("A single request can cover at most 60 days")
        if self.start_date != self.end_date and (self.entry_time or self.exit_time):
            raise ValueError("Entry/exit times are only allowed for single-day requests")
        if (self.entry_time is None) != (self.exit_time is None):
            raise ValueError("Provide both entry_time and exit_time, or neither")
        if self.entry_time and self.exit_time and self.entry_time >= self.exit_time:
            raise ValueError("entry_time must be before exit_time")
        return self


class RequestOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str | None = None
    reason: str
    start_date: date
    end_date: date
    entry_time: str | None
    exit_time: str | None
    kind: Literal["correction", "leave"]
    status: Literal["Pending", "Approved", "Rejected"]
    submitted_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    comment: str | None = None


class DecisionIn(BaseModel):
    action: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1000)
