"""Presence tracking: beacon matching, interval crediting and the atomic session update.

The matching and crediting rules are pure functions so they can be unit-tested without a
database. See docs/api-contract-v2.md ("Presence and sessions").
"""

from __future__ import annotations

import uuid as uuidlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

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


# ---------- live board status (pure) ----------

BoardStatus = Literal["present", "away", "outside", "stale", "not_checked_in", "on_leave"]

# Display order of the presence board.
STATUS_ORDER: tuple[BoardStatus, ...] = (
    "present",
    "away",
    "stale",
    "outside",
    "not_checked_in",
    "on_leave",
)


def latest_app_session(sessions: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The session that describes an employee's current state: the open ``app`` session if there
    is one (the newest, should there be several), otherwise the newest ``app`` session given."""
    app_sessions = [s for s in sessions if s.get("source", "app") == "app"]
    if not app_sessions:
        return None
    open_ones = [s for s in app_sessions if s.get("ended_at") is None]
    pool = open_ones or app_sessions
    return max(pool, key=lambda s: as_utc(s["started_at"]))


def board_status(
    latest: Mapping[str, Any] | None,
    on_leave: bool,
    now: datetime,
    grace_seconds: float,
) -> BoardStatus:
    """Live status of one employee.

    ``latest`` is ``latest_app_session`` of their open session / today's sessions.
    - open session, no heartbeat for longer than the grace window -> ``stale``
    - open session otherwise -> its last state (``present``/``away``)
    - not checked in: ``on_leave`` if an approved leave covers today, ``outside`` if today's last
      session was closed because the phone left the geofence, else ``not_checked_in``
    """
    if latest is not None and latest.get("ended_at") is None:
        last = as_utc(latest.get("last_heartbeat_at") or latest["started_at"])
        if (as_utc(now) - last).total_seconds() > grace_seconds:
            return "stale"
        return latest.get("last_state") or "present"
    if on_leave:
        return "on_leave"
    if latest is not None and latest.get("last_state") == "outside":
        return "outside"
    return "not_checked_in"


def day_status(attended: bool, on_leave: bool) -> BoardStatus:
    """Status used for a past day on the dashboard, where "live" has no meaning: anyone with a
    session that day counts as ``present``."""
    if attended:
        return "present"
    return "on_leave" if on_leave else "not_checked_in"


def has_beacon(user: Mapping[str, Any], beacons: Iterable[Mapping[str, Any]]) -> bool:
    """Would ``/beacons/mine`` return anything for this user? Mirrors ``beacons_for_user``."""
    active = [b for b in beacons if b.get("active", True) is not False]
    if any(b.get("assigned_employee_id") == user["employee_id"] for b in active):
        return True
    office_id = user.get("office_id")
    return bool(office_id) and any(
        b.get("office_id") == office_id and b.get("assigned_employee_id") is None for b in active
    )


def is_late(first_check_in: datetime, work_start: str, tz: ZoneInfo) -> bool:
    """Arrived after office start. Compared at minute precision in office-local time, so
    09:30:40 is not late for a 09:30 start but 09:31 is."""
    local = as_utc(first_check_in).astimezone(tz)
    return local.time().replace(second=0, microsecond=0) > time.fromisoformat(work_start)


def last_heartbeat_rssi(session: Mapping[str, Any]) -> int | None:
    """Signal strength of the newest presence-log entry (check-in's if there is none yet)."""
    log: Sequence[Mapping[str, Any]] = session.get("presence_log") or []
    if log:
        return log[-1].get("best_rssi")
    return (session.get("check_in") or {}).get("best_rssi")


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
