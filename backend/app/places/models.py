from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, LargeBinary, Text, Time, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..core.models import Base


class Office(Base):
    __tablename__ = "offices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    work_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeskDevice(Base):
    __tablename__ = "desk_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    office_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    ibeacon_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    major: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    device: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    device_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    pairing_code_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
