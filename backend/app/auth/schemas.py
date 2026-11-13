from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from ..core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from ..people.schemas import UserOut

Password = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginIn(BaseModel):
    """Either ``email`` or ``org_slug`` + ``employee_code``."""

    email: EmailStr | None = None
    org_slug: str | None = Field(default=None, max_length=80)
    employee_code: str | None = Field(default=None, max_length=80)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    device_label: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _identity(self):
        if self.email is None and not (self.org_slug and self.employee_code):
            raise ValueError("Provide email, or org_slug and employee_code")
        return self


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Password


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Password


class InviteAcceptIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Password


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_label: str | None
    ip: str | None
    user_agent: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool = False


class ConsentAcceptIn(BaseModel):
    version: int


class ConsentTextOut(BaseModel):
    version: int
    title: str
    body_markdown: str
    accepted: bool


class MessageOut(BaseModel):
    message: str


class LoginOut(TokenPair):
    user: UserOut
    consent_required: bool
