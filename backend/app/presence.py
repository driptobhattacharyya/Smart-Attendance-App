"""Presence tracking: beacon matching, interval crediting and the atomic session update.

The matching and crediting rules are pure functions so they can be unit-tested without a
database. See docs/api-contract-v2.md ("Presence and sessions").
"""

from __future__ import annotations

import uuid as uuidlib
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal

from .db import Database, as_utc

PresenceStateName = Literal["present", "away", "outside"]

PRESENCE_LOG_LIMIT = 500
MAX_UPDATE_ATTEMPTS = 3


# ---------- beacon matching (pure) ----------


def canonical_uuid(value: str | None) -> str | None:
    """Lowercase dashed UUID, or None if ``value`` is not a UUID."""
    if not value:
        return None
    try:
        return str(uuidlib.UUID(value.strip()))
    except ValueError:
        return None


def reading_matches(reading: Mapping[str, Any], beacon: Mapping[str, Any]) -> bool:
    """uuid equal (case-insensitive); major/minor equal or null on the beacon (wildcard)."""
    reading_uuid = canonical_uuid(reading.get("uuid"))
    if reading_uuid is None or reading_uuid != canonical_uuid(beacon.get("uuid")):
        return False
    for key in ("major", "minor"):
        wanted = beacon.get(key)
        if wanted is not None and reading.get(key) != wanted:
            return False
    return True


def best_rssi(
    readings: Iterable[Mapping[str, Any]], beacons: Iterable[Mapping[str, Any]]
) -> int | None:
    """Strongest signal among readings that match any of ``beacons`` (None if none match)."""
    beacons = list(beacons)
    matching = [r["rssi"] for r in readings if any(reading_matches(r, b) for b in beacons)]
    return max(matching) if matching else None


def is_near(best: int | None, min_rssi: int) -> bool:
    return best is not None and best >= min_rssi


def decide_state(inside: bool, near: bool, require_bluetooth: bool) -> PresenceStateName:
    if not inside:
        return "outside"
    if near or not require_bluetooth:
        return "present"
    return "away"


# ---------- crediting (pure) ----------


def credit_interval(
    previous_state: str | None,
    last_heartbeat_at: datetime,
    now: datetime,
    grace_seconds: float,
) -> tuple[int, int]:
    """Seconds to add to (present, away) for the interval since the last heartbeat.

    The interval belongs to the *previous* state and is capped at the grace window: if the phone
    went silent for longer, the excess is not counted at all.
    """
    gap = (as_utc(now) - as_utc(last_heartbeat_at)).total_seconds()
    credited = int(max(0.0, min(gap, grace_seconds)))
    if previous_state == "present":
        return credited, 0
    return 0, credited


def worked_seconds(
    doc: Mapping[str, Any], now: datetime, grace_seconds: float, max_seconds: float
) -> int:
    """Seconds that count as worked for an ``app`` session (present time only).

    Open sessions also count the not-yet-credited tail since the last heartbeat while the last
    known state is ``present``, capped at the grace window. Open sessions are capped at
    ``max_seconds`` overall.
    """
    seconds = int(doc.get("present_seconds") or 0)
    if not doc.get("ended_at"):
        if doc.get("last_state") == "present" and doc.get("last_heartbeat_at"):
            pending, _ = credit_interval("present", doc["last_heartbeat_at"], now, grace_seconds)
            seconds += pending
        seconds = min(seconds, int(max_seconds))
    return seconds


# ---------- atomic session update ----------


class SessionClosedError(Exception):
    """The session was ended (check-out, auto check-out) before the update could apply."""


async def apply_presence(
    db: Database,
    session_id: Any,
    now: datetime,
    grace_seconds: float,
    *,
    state: PresenceStateName | None,
    log_entry: dict | None = None,
    end: bool = False,
    extra_set: dict | None = None,
) -> dict:
    """Credit the interval since the last heartbeat and store the new state, atomically.

    The update only matches while the session is open *and* ``last_heartbeat_at`` is still the
    value we read, and uses ``$inc``, so two concurrent heartbeats can never credit the same
    interval twice: the loser re-reads and credits only what is left. ``state=None`` keeps the
    last state (check-out). Returns the updated session document; raises
    ``SessionClosedError`` if the session is already closed.
    """
    for _ in range(MAX_UPDATE_ATTEMPTS):
        doc = await db.attendance_sessions.find_one({"_id": session_id})
        if doc is None or doc.get("ended_at") is not None:
            raise SessionClosedError
        last_at = as_utc(doc.get("last_heartbeat_at") or doc["started_at"])
        present, away = credit_interval(doc.get("last_state"), last_at, now, grace_seconds)

        fields: dict[str, Any] = {"last_heartbeat_at": now}
        if state is not None:
            fields["last_state"] = state
        if end:
            fields["ended_at"] = now
        if extra_set:
            fields.update(extra_set)
        update: dict[str, Any] = {
            "$set": fields,
            "$inc": {"present_seconds": present, "away_seconds": away},
        }
        if log_entry is not None:
            update["$push"] = {
                "presence_log": {"$each": [log_entry], "$slice": -PRESENCE_LOG_LIMIT}
            }

        # Compare with the stored value (Mongo keeps milliseconds), not our own.
        stored_last = doc.get("last_heartbeat_at")
        guard: dict[str, Any] = {"_id": session_id, "ended_at": None}
        guard["last_heartbeat_at"] = stored_last if stored_last is not None else {"$exists": False}
        result = await db.attendance_sessions.update_one(guard, update)
        if result.matched_count == 1:
            return await db.attendance_sessions.find_one({"_id": session_id})
    raise SessionClosedError
