from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserOut(BaseModel):
    """Never carries password_hash / mfa secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_code: str
    email: str
    name: str
    phone: str | None
    status: str
    role_key: str
    manager_id: uuid.UUID | None
    department_id: uuid.UUID | None
    office_id: uuid.UUID | None
    last_login_at: datetime | None
    created_at: datetime


class OrgBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    modules: dict[str, Any]


class MeOut(UserOut):
    permissions: list[str]
    org: OrgBrief
    consent_required: bool
    consent_version: int


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    employee_code: str | None = Field(default=None, min_length=1, max_length=40)
    phone: str | None = Field(default=None, max_length=40)
    role_key: str = Field(default="employee", max_length=40)
    manager_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    """Only fields present in the request change (null clears manager/department/office)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    employee_code: str | None = Field(default=None, min_length=1, max_length=40)
    phone: str | None = Field(default=None, max_length=40)
    role_key: str | None = Field(default=None, max_length=40)
    manager_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None


class MeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


UserStatus = Literal["active", "invited", "disabled"]
