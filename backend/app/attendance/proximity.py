"""Desk-proximity evaluation and the presence state decision. Pure apart from the loader.

Ported from the single-tenant ``presence.py`` (git 8d6aa78) and adapted to the v5 devices: tags
have a fixed identity, desk apps broadcast a rotating one (``places.identity``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import Settings
from ..core.security import utcnow
from ..people.models import User
from ..places.desk_system import decrypt_device_secret
from ..places.identity import match_devices
from ..places.models import DeskDevice
from .rules import PolicyRules
from .schemas import BeaconReading

# Higher = more informative when several desk computers block the same readings.
_DESK_REASON_RANK = {"desk_offline": 0, "desk_idle": 1, "desk_locked": 2}


@dataclass(frozen=True)
class Candidate:
    """A device whose broadcast counts as "I am at my desk" for one user."""

    id: uuid.UUID
    kind: str  # tag | desk_app
    ibeacon_uuid: uuid.UUID | None  # tags: fixed uuid
    major: int | None
    minor: int | None
    secret: bytes | None  # desk apps: rotating identity secret
    paired: bool
    last_seen_at: datetime | None
    state: dict[str, Any] = field(default_factory=dict)  # locked / idle_seconds / ...


@dataclass(frozen=True)
class SignalResult:
    best_rssi: int | None  # strongest matching reading, whether or not it counts
    near: bool
    reason: str  # near | no_signal | weak_signal | desk_locked | desk_idle | desk_offline
    device_ids: tuple[uuid.UUID, ...] = ()


def desk_block_reason(
    device: Candidate,
    now: datetime,
    *,
    mode: str,
    idle_limit_seconds: float,
    offline_after_seconds: float,
) -> str | None:
    """Why a desk app does not currently count as "the employee is at the desk". ``None`` for
    tags, when monitoring is off, and for a desk computer that is online, unlocked and not idle
    for too long."""
    if device.kind != "desk_app" or mode == "off":
        return None
    if (
        not device.paired
        or device.last_seen_at is None
        or (now - device.last_seen_at).total_seconds() > offline_after_seconds
    ):
        return "desk_offline"
    if device.state.get("locked"):
        return "desk_locked"
    if mode == "locked_or_idle" and int(device.state.get("idle_seconds") or 0) > idle_limit_seconds:
        return "desk_idle"
    return None


def match_readings(
    readings: Iterable[BeaconReading],
    candidates: Sequence[Candidate],
    desk_uuid: uuid.UUID | None,
    at: datetime,
) -> list[tuple[int, list[Candidate]]]:
    """For each reading, the candidates it identifies (empty readings are dropped). Desk apps
    match through ``match_devices`` over windows w-1..w+1 around ``at`` (the receive time)."""
    by_id = {c.id: c for c in candidates}
    secrets = [(c.id, c.secret) for c in candidates if c.kind == "desk_app" and c.secret]
    out: list[tuple[int, list[Candidate]]] = []
    for r in readings:
        matched: list[Candidate] = [
            c
            for c in candidates
            if c.kind == "tag"
            and c.ibeacon_uuid == r.uuid
            and c.major == r.major
            and c.minor == r.minor
        ]
        if desk_uuid is not None and r.uuid == desk_uuid:
            matched += [by_id[i] for i in match_devices(secrets, r.major, r.minor, at)]
        if matched:
            out.append((r.rssi, matched))
    return out


def evaluate_signal(
    matches: Sequence[tuple[int, list[Candidate]]],
    rules: PolicyRules,
    now: datetime,
    *,
    desk_mode: str,
    offline_after_seconds: float,
) -> SignalResult:
    counted: list[int] = []
    blocked: list[tuple[int, str]] = []
    all_rssi: list[int] = []
    ids: list[uuid.UUID] = []
    for rssi, devices in matches:
        all_rssi.append(rssi)
        reasons = [
            desk_block_reason(
                d,
                now,
                mode=desk_mode,
                idle_limit_seconds=rules.desk_idle_limit_minutes * 60,
                offline_after_seconds=offline_after_seconds,
            )
            for d in devices
        ]
        if any(r is None for r in reasons):
            counted.append(rssi)
            ids += [d.id for d, r in zip(devices, reasons, strict=True) if r is None]
        elif rssi >= rules.min_rssi:
            blocked.append(
                (rssi, max((r for r in reasons if r), key=_DESK_REASON_RANK.__getitem__))
            )
    best = max(all_rssi) if all_rssi else None
    if counted and max(counted) >= rules.min_rssi:
        return SignalResult(best, True, "near", tuple(dict.fromkeys(ids)))
    if blocked:
        return SignalResult(best, False, max(blocked)[1])
    return SignalResult(best, False, "weak_signal" if all_rssi else "no_signal")


def decide_state(inside: bool, near: bool, require_proximity: bool) -> str:
    if not inside:
        return "outside"
    if near or not require_proximity:
        return "present"
    return "away"


def presence_reason(inside: bool, signal: SignalResult | None, require_proximity: bool) -> str:
    if not inside:
        return "outside"
    if not require_proximity or signal is None:
        return "inside"
    return signal.reason


def effective_desk_mode(rules: PolicyRules, org_settings: dict[str, Any]) -> str:
    """Desk activity monitoring needs both the policy rule and the explicit org opt-in (which the
    consent text discloses)."""
    return rules.desk_activity if org_settings.get("desk_activity_monitoring") else "off"


async def load_candidates(db: AsyncSession, settings: Settings, user: User) -> list[Candidate]:
    """Active devices the user's phone may be near: assigned to them, or unassigned and in
    their office (or office-less)."""
    office_filter = DeskDevice.office_id.is_(None)
    if user.office_id:
        office_filter = or_(office_filter, DeskDevice.office_id == user.office_id)
    rows = (
        (
            await db.execute(
                select(DeskDevice).where(
                    DeskDevice.active.is_(True),
                    or_(
                        DeskDevice.assigned_user_id == user.id,
                        (DeskDevice.assigned_user_id.is_(None)) & office_filter,
                    ),
                    or_(DeskDevice.kind == "tag", DeskDevice.paired_at.is_not(None)),
                )
            )
        )
        .scalars()
        .all()
    )
    out: list[Candidate] = []
    for d in rows:
        secret = None
        if d.kind == "desk_app" and d.secret_enc:
            secret = decrypt_device_secret(settings, d.id, d.secret_enc)
        out.append(
            Candidate(
                id=d.id,
                kind=d.kind,
                ibeacon_uuid=d.ibeacon_uuid,
                major=d.major,
                minor=d.minor,
                secret=secret,
                paired=d.paired_at is not None and d.device_token_hash is not None,
                last_seen_at=d.last_seen_at,
                state=dict(d.device or {}),
            )
        )
    return out


def evaluate_for_user(
    candidates: Sequence[Candidate],
    readings: Sequence[BeaconReading],
    org_settings: dict[str, Any],
    rules: PolicyRules,
    settings: Settings,
    now: datetime | None = None,
) -> SignalResult:
    now = now or utcnow()
    desk_uuid = org_settings.get("desk_uuid")
    matches = match_readings(readings, candidates, uuid.UUID(desk_uuid) if desk_uuid else None, now)
    return evaluate_signal(
        matches,
        rules,
        now,
        desk_mode=effective_desk_mode(rules, org_settings),
        offline_after_seconds=settings.desk_offline_after_seconds,
    )
