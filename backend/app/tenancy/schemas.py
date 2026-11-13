from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..auth.schemas import TokenPair
from ..core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from ..people.schemas import UserOut


class SignupIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    phone: str | None = Field(default=None, max_length=40)


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: str
    modules: dict[str, Any]
    settings: dict[str, Any]
    consent_version: int
    created_at: datetime


class SignupOut(TokenPair):
    org: OrgOut
    user: UserOut
    consent_required: bool = True


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    # Partial: only the listed module flags change. Unknown keys are rejected.
    modules: dict[str, bool] | None = None
    # Partial: only whitelisted keys (see tenancy.service.EDITABLE_SETTINGS).
    settings: dict[str, Any] | None = None
    # Everyone must accept the consent text again.
    bump_consent_version: bool = False
