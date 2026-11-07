"""Data retention: presence details are only kept for PRESENCE_RETENTION_DAYS.

Old sessions keep their date, times and worked/away totals; the detailed location trail
(``presence_log``, ``check_in`` and ``check_out`` coordinates) is removed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .config import Settings
from .db import Database

logger = logging.getLogger("attendify.retention")

PRUNE_INTERVAL = timedelta(hours=1)
DETAIL_FIELDS = ("presence_log", "check_in", "check_out")

_last_run: datetime | None = None  # per process


def cutoff_date(now: datetime, settings: Settings) -> str:
    """Sessions dated before this (ISO date) are past retention."""
    return (now.date() - timedelta(days=settings.presence_retention_days)).isoformat()


async def prune_presence(db: Database, settings: Settings, now: datetime) -> int:
    """Unset the detail fields of sessions older than the retention window. Returns the number
    of sessions changed (so a second run returns 0)."""
    result = await db.attendance_sessions.update_many(
        {
            "date": {"$lt": cutoff_date(now, settings)},
            "$or": [{field: {"$exists": True}} for field in DETAIL_FIELDS],
        },
        {"$unset": {field: "" for field in DETAIL_FIELDS}},
    )
    return result.modified_count


async def maybe_prune(db: Database, settings: Settings, now: datetime) -> int | None:
    """Prune at most once per hour per process. Never raises: housekeeping must not break the
    request that triggered it. Returns the count, or None if it did not run."""
    global _last_run
    if _last_run is not None and now - _last_run < PRUNE_INTERVAL:
        return None
    _last_run = now
    try:
        return await prune_presence(db, settings, now)
    except Exception:
        logger.exception("Pruning old presence data failed")
        return None
