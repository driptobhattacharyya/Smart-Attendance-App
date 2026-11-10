"""Desk app flows authenticated by a pairing code / device token (system session).

There is no user and no tenant on these requests; the device row itself says which org it is in.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import timedelta

from sqlalchemy import select

from ..audit.service import write_audit
from ..auth import ratelimit
from ..core import crypto
from ..core.config import Settings
from ..core.db import Database
from ..core.errors import AppError, forbidden
from ..core.security import new_token, sha256_hex, utcnow
from ..people.models import User
from ..tenancy.models import Organization
from .identity import SECRET_BYTES, WINDOW_SECONDS
from .models import DeskDevice
from .schemas import (
    DeskBeaconBrief,
    DeskEmployee,
    DeskHeartbeatIn,
    DeskHeartbeatOut,
    DeskPairIn,
    DeskPairOut,
)
from .service import normalize_code

DEVICE_TOKEN_PREFIX = "adt_"
INVALID_CODE = "Invalid or expired code"


def device_aad(device_id: uuid.UUID) -> bytes:
    return b"desk_device:" + device_id.bytes


def encrypt_device_secret(settings: Settings, device_id: uuid.UUID, secret: bytes) -> bytes:
    return crypto.encrypt(settings.data_key_bytes, secret, device_aad(device_id))


def decrypt_device_secret(settings: Settings, device_id: uuid.UUID, blob: bytes) -> bytes:
    return crypto.decrypt(settings.data_key_bytes, blob, device_aad(device_id))


async def pair_device(
    database: Database, settings: Settings, data: DeskPairIn, *, ip: str | None
) -> DeskPairOut:
    """Trade a one-time pairing code for a device token and the rotating-identity secret.
    Pairing again replaces both (a cloned broadcaster stops matching)."""
    code_hash = sha256_hex(normalize_code(data.code))
    async with database.system_session() as s:
        if ip:
            await ratelimit.enforce(
                s,
                "pair",
                [f"ip:{ip}"],
                limit=settings.pair_max_failures,
                window=timedelta(minutes=15),
            )
        device = (
            await s.execute(
                select(DeskDevice)
                .where(
                    DeskDevice.pairing_code_hash == code_hash,
                    DeskDevice.pairing_expires_at > utcnow(),
                    DeskDevice.kind == "desk_app",
                    DeskDevice.active.is_(True),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if device is None:
            if ip:
                await ratelimit.record(s, "pair", f"ip:{ip}")
            await s.commit()
            raise AppError(404, "invalid_code", INVALID_CODE)

        org = (
            await s.execute(select(Organization).where(Organization.id == device.org_id))
        ).scalar_one()
        if org.status != "active":
            raise forbidden("This organization is suspended", "org_suspended")

        token = new_token(DEVICE_TOKEN_PREFIX)
        secret = os.urandom(SECRET_BYTES)
        now = utcnow()
        device.device_token_hash = sha256_hex(token)
        device.secret_enc = encrypt_device_secret(settings, device.id, secret)
        device.pairing_code_hash = None  # single use
        device.pairing_expires_at = None
        device.paired_at = now
        device.last_seen_at = now
        device.device = {
            "hostname": data.hostname,
            "os": data.os,
            "agent_version": data.agent_version,
            "locked": False,
            "idle_seconds": 0,
            "advertising": False,
        }
        employee = None
        if device.assigned_user_id:
            user = (
                await s.execute(select(User).where(User.id == device.assigned_user_id))
            ).scalar_one_or_none()
            if user:
                employee = DeskEmployee(
                    id=user.id, employee_code=user.employee_code, name=user.name
                )
        await write_audit(
            s,
            org_id=device.org_id,
            actor_kind="device",
            action="device.paired",
            target_type="desk_device",
            target_id=device.id,
            ip=ip,
            after={"hostname": data.hostname, "os": data.os},
        )
        return DeskPairOut(
            device_token=token,
            secret=base64.b64encode(secret).decode(),
            beacon=DeskBeaconBrief(
                id=device.id,
                name=device.name,
                uuid=uuid.UUID(org.settings["desk_uuid"]),
                window_seconds=WINDOW_SECONDS,
            ),
            employee=employee,
            heartbeat_interval_seconds=settings.desk_heartbeat_interval_seconds,
            server_time=now,
        )


async def device_heartbeat(
    database: Database, settings: Settings, token: str, data: DeskHeartbeatIn
) -> DeskHeartbeatOut:
    wanted = sha256_hex(token)
    async with database.system_session() as s:
        device = (
            await s.execute(select(DeskDevice).where(DeskDevice.device_token_hash == wanted))
        ).scalar_one_or_none()
        if device is None or not device.active:
            raise AppError(
                401,
                "device_not_paired",
                "Device not paired",
                headers={"WWW-Authenticate": "Device"},
            )
        org_status = (
            await s.execute(select(Organization.status).where(Organization.id == device.org_id))
        ).scalar_one()
        if org_status != "active":
            raise forbidden("This organization is suspended", "org_suspended")
        now = utcnow()
        state = dict(device.device or {})
        state.update(
            locked=data.locked, idle_seconds=data.idle_seconds, advertising=data.advertising
        )
        if data.agent_version:
            state["agent_version"] = data.agent_version
        device.device = state
        device.last_seen_at = now
        return DeskHeartbeatOut(
            server_time=now, heartbeat_interval_seconds=settings.desk_heartbeat_interval_seconds
        )
