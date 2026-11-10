"""Offices and desk devices (tenant session). Device-token flows are in desk_system.py."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..auth.deps import Ctx
from ..core.errors import bad_request, conflict, forbidden, not_found
from ..core.security import sha256_hex, utcnow
from ..people.models import User
from .models import DeskDevice, Office
from .schemas import DeskDeviceCreate, DeskDeviceOut, DeskDeviceUpdate, OfficeCreate, OfficeUpdate

# No 0/O/1/I/L: nothing that can be misread when typed from a screen.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize_code(value: str) -> str:
    """What the admin read out, however it was typed: upper case, no dash or spaces."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def office_snapshot(o: Office) -> dict[str, Any]:
    return {
        c: getattr(o, c)
        for c in (
            "name latitude longitude radius_m timezone address work_start work_end active".split()
        )
    }


# ------------------------------------------------------------------------------- offices


async def list_offices(ctx: Ctx) -> list[Office]:
    return list((await ctx.db.execute(select(Office).order_by(func.lower(Office.name)))).scalars())


async def get_office(ctx: Ctx, office_id: uuid.UUID) -> Office:
    office = (
        await ctx.db.execute(select(Office).where(Office.id == office_id))
    ).scalar_one_or_none()
    if office is None:
        raise not_found("Office")
    return office


async def create_office(ctx: Ctx, data: OfficeCreate) -> Office:
    office = Office(id=uuid.uuid4(), org_id=ctx.org_id, **data.model_dump())
    ctx.db.add(office)
    await ctx.db.flush()
    await ctx.audit("office.create", "office", office.id, after=office_snapshot(office))
    return office


async def update_office(ctx: Ctx, office_id: uuid.UUID, data: OfficeUpdate) -> Office:
    office = await get_office(ctx, office_id)
    changes = data.model_dump(exclude_unset=True)
    for required in ("name", "latitude", "longitude", "radius_m", "timezone", "active"):
        if required in changes and changes[required] is None:
            raise bad_request("invalid_field", f"{required} cannot be null")
    before = office_snapshot(office)
    for key, value in changes.items():
        setattr(office, key, value)
    await ctx.db.flush()
    await ctx.audit("office.update", "office", office.id, before, office_snapshot(office))
    return office


async def delete_office(ctx: Ctx, office_id: uuid.UUID) -> None:
    office = await get_office(ctx, office_id)
    in_use = (
        await ctx.db.execute(
            select(
                select(func.count())
                .select_from(User)
                .where(User.office_id == office_id)
                .scalar_subquery()
                + select(func.count())
                .select_from(DeskDevice)
                .where(DeskDevice.office_id == office_id)
                .scalar_subquery()
            )
        )
    ).scalar_one()
    if in_use:
        raise conflict(
            "office_in_use",
            "People or devices still use this office; deactivate it instead",
            references=in_use,
        )
    await ctx.db.delete(office)
    await ctx.db.flush()
    await ctx.audit("office.delete", "office", office_id, before=office_snapshot(office))


# ------------------------------------------------------------------------ desk devices


def device_out(device: DeskDevice, desk_uuid: str) -> DeskDeviceOut:
    is_tag = device.kind == "tag"
    return DeskDeviceOut(
        id=device.id,
        kind=device.kind,
        name=device.name,
        office_id=device.office_id,
        assigned_user_id=device.assigned_user_id,
        active=device.active,
        ibeacon_uuid=device.ibeacon_uuid if is_tag else uuid.UUID(desk_uuid),
        major=device.major if is_tag else None,
        minor=device.minor if is_tag else None,
        paired=device.paired_at is not None and device.device_token_hash is not None,
        paired_at=device.paired_at,
        last_seen_at=device.last_seen_at,
        device=device.device or {},
    )


def device_snapshot(d: DeskDevice) -> dict[str, Any]:
    return {
        "kind": d.kind,
        "name": d.name,
        "office_id": d.office_id,
        "assigned_user_id": d.assigned_user_id,
        "active": d.active,
        "ibeacon_uuid": d.ibeacon_uuid if d.kind == "tag" else None,
        "major": d.major if d.kind == "tag" else None,
        "minor": d.minor if d.kind == "tag" else None,
        "paired": d.paired_at is not None,
    }


async def _check_device_refs(
    ctx: Ctx, office_id: uuid.UUID | None, assigned_user_id: uuid.UUID | None
) -> None:
    if office_id is not None:
        await get_office(ctx, office_id)
    if assigned_user_id is not None:
        found = (await ctx.db.execute(select(User.id).where(User.id == assigned_user_id))).first()
        if not found:
            raise bad_request("unknown_user", "Assigned user not found")


async def get_device(ctx: Ctx, device_id: uuid.UUID) -> DeskDevice:
    device = (
        await ctx.db.execute(select(DeskDevice).where(DeskDevice.id == device_id))
    ).scalar_one_or_none()
    if device is None:
        raise not_found("Desk device")
    return device


async def list_devices(ctx: Ctx, kind: str | None = None) -> list[DeskDevice]:
    stmt = select(DeskDevice).order_by(DeskDevice.kind, func.lower(DeskDevice.name))
    if kind:
        stmt = stmt.where(DeskDevice.kind == kind)
    return list((await ctx.db.execute(stmt)).scalars())


async def devices_for_me(ctx: Ctx) -> list[DeskDeviceOut]:
    """What a phone scans for: tags (exact identity) and paired desk apps (wildcard
    major/minor, the org desk UUID). Devices assigned to somebody else are left out."""
    rows = (
        await ctx.db.execute(
            select(DeskDevice)
            .where(
                DeskDevice.active.is_(True),
                or_(
                    DeskDevice.assigned_user_id.is_(None),
                    DeskDevice.assigned_user_id == ctx.user_id,
                ),
                or_(DeskDevice.kind == "tag", DeskDevice.paired_at.is_not(None)),
            )
            .order_by(DeskDevice.kind, DeskDevice.name)
        )
    ).scalars()
    desk_uuid = ctx.org.settings["desk_uuid"]
    return [device_out(d, desk_uuid) for d in rows]


async def create_device(ctx: Ctx, data: DeskDeviceCreate) -> DeskDevice:
    if data.kind == "desk_app" and not ctx.org.modules.get("desk_app"):
        raise forbidden(
            "The 'desk_app' module is not enabled", "module_disabled", module="desk_app"
        )
    await _check_device_refs(ctx, data.office_id, data.assigned_user_id)
    device = DeskDevice(
        id=uuid.uuid4(),
        org_id=ctx.org_id,
        kind=data.kind,
        name=data.name.strip(),
        office_id=data.office_id,
        assigned_user_id=data.assigned_user_id,
        ibeacon_uuid=data.ibeacon_uuid,
        major=data.major,
        minor=data.minor,
        device={},
    )
    ctx.db.add(device)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise conflict("beacon_identity_taken", "That uuid/major/minor is already in use") from exc
    await ctx.audit("device.create", "desk_device", device.id, after=device_snapshot(device))
    return device


async def update_device(ctx: Ctx, device_id: uuid.UUID, data: DeskDeviceUpdate) -> DeskDevice:
    device = await get_device(ctx, device_id)
    changes = data.model_dump(exclude_unset=True)
    if device.kind != "tag" and {"ibeacon_uuid", "major", "minor"} & changes.keys():
        raise bad_request("not_a_tag", "Only tags have a fixed identity")
    for required in ("name", "active"):
        if required in changes and changes[required] is None:
            raise bad_request("invalid_field", f"{required} cannot be null")
    if device.kind == "tag":
        for key in ("ibeacon_uuid", "major", "minor"):
            if key in changes and changes[key] is None:
                raise bad_request("invalid_field", f"{key} cannot be null for a tag")
    await _check_device_refs(ctx, changes.get("office_id"), changes.get("assigned_user_id"))
    before = device_snapshot(device)
    for key, value in changes.items():
        setattr(device, key, value)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise conflict("beacon_identity_taken", "That uuid/major/minor is already in use") from exc
    await ctx.audit("device.update", "desk_device", device.id, before, device_snapshot(device))
    return device


async def delete_device(ctx: Ctx, device_id: uuid.UUID) -> None:
    device = await get_device(ctx, device_id)
    await ctx.db.delete(device)
    await ctx.db.flush()
    await ctx.audit("device.delete", "desk_device", device_id, before=device_snapshot(device))


async def create_pairing_code(ctx: Ctx, device_id: uuid.UUID) -> tuple[str, Any]:
    device = await get_device(ctx, device_id)
    if device.kind != "desk_app":
        raise bad_request("not_a_desk_app", "Only desk apps are paired")
    expires = utcnow() + timedelta(minutes=ctx.settings.desk_pairing_ttl_minutes)
    for _ in range(10):
        code = generate_code()
        try:
            async with ctx.db.begin_nested():
                await ctx.db.execute(
                    update(DeskDevice)
                    .where(DeskDevice.id == device.id)
                    .values(pairing_code_hash=sha256_hex(code), pairing_expires_at=expires)
                )
            break
        except IntegrityError:  # astronomically unlikely code collision with another device
            continue
    else:
        raise conflict("code_generation_failed", "Could not generate a pairing code")
    await ctx.audit("device.pairing_code", "desk_device", device.id, after={"expires_at": expires})
    return format_code(code), expires


async def unpair_device(ctx: Ctx, device_id: uuid.UUID) -> DeskDevice:
    device = await get_device(ctx, device_id)
    if device.kind != "desk_app":
        raise bad_request("not_a_desk_app", "Only desk apps are paired")
    device.device_token_hash = None
    device.secret_enc = None
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.paired_at = None
    device.device = {}
    await ctx.db.flush()
    await ctx.audit("device.unpair", "desk_device", device.id)
    return device
