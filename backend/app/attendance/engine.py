"""The calculation engine: a pure function from events to a day record.

``compute_day`` has no database, clock or global state (``now`` is a parameter), so it is
table-tested exhaustively. Events are the only source of truth; ``attendance_days`` is a cache
that can be rebuilt from them at any time (see ``recompute.py`` and the ``recompute`` job).

Rules worth knowing:

* A session starts with ``check_in`` / ``remote_check_in`` and ends with ``check_out`` /
  ``auto_check_out``. The caller passes every event *attributed to* ``day``: a session that
  crosses midnight belongs to the day it started on, and so do all its later events.
* ``presence_changed`` events cut a session into present / away / outside segments.
* Counting modes: ``presence`` counts only present segments; ``span`` counts check-in to
  check-out whatever the state; ``span_minus_away`` counts the whole span minus away and outside
  segments. (With the event vocabulary of step 2 the first and last agree numerically; they are
  separate settings so a policy states its intent and they can diverge later.) ``away_minutes``
  is reported in every mode.
* Corrections never edit history: ``interval_added`` adds a work or break interval, and
  ``event_voided`` removes an event from the calculation (voiding a session start removes the
  whole session).
* An open session counts up to ``min(now, last evidence + grace, start + max_session_hours)``.
  Once the evidence is older than the grace window (or the maximum session length is reached) the
  day is ``incomplete`` with ``missing_check_out`` until the close-stale-sessions job (or the
  user's next request) closes it with an ``auto_check_out``.
* Late / early leave compare absolute instants against the office-local working hours of ``day``
  (``zoneinfo``), so DST changes cannot shift them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import CLOSE_TYPES, START_TYPES
from .rules import PolicyRules

Interval = tuple[datetime, datetime]

CLOSE_REASONS_MISSING = ("stale", "max_session")


@dataclass(frozen=True)
class Event:
    id: uuid.UUID
    type: str
    occurred_at: datetime
    recorded_at: datetime
    session_id: uuid.UUID | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    voids_event_id: uuid.UUID | None = None


@dataclass(frozen=True)
class OfficeInfo:
    timezone: str = "UTC"
    work_start: time | None = None
    work_end: time | None = None


@dataclass(frozen=True)
class SamplesSummary:
    """Latest presence sample time per session (heartbeats are not events)."""

    last_at: Mapping[uuid.UUID, datetime] = field(default_factory=dict)


@dataclass
class SessionInfo:
    id: uuid.UUID
    mode: str
    started_at: datetime
    ended_at: datetime | None
    closed_by: str | None
    close_reason: str | None
    worked_minutes: int
    away_minutes: int
    open: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "closed_by": self.closed_by,
            "close_reason": self.close_reason,
            "worked_minutes": self.worked_minutes,
            "away_minutes": self.away_minutes,
            "open": self.open,
        }


@dataclass
class DayRecord:
    day: date
    first_in: datetime | None
    last_out: datetime | None
    worked_minutes: int
    away_minutes: int
    break_minutes: int
    late_minutes: int
    early_leave_minutes: int
    overtime_minutes: int
    status: str
    flags: list[str]
    sessions: list[SessionInfo]
    has_activity: bool

    @property
    def session_count(self) -> int:
        return len(self.sessions)


# ------------------------------------------------------------------------ interval algebra


def _union(intervals: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _subtract(base: Sequence[Interval], cut: Sequence[Interval]) -> list[Interval]:
    out: list[Interval] = []
    cuts = _union(cut)
    for start, end in base:
        cursor = start
        for c_start, c_end in cuts:
            if c_end <= cursor or c_start >= end:
                continue
            if c_start > cursor:
                out.append((cursor, c_start))
            cursor = max(cursor, c_end)
            if cursor >= end:
                break
        if cursor < end:
            out.append((cursor, end))
    return out


def _intersect(a: Sequence[Interval], b: Sequence[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a_start, a_end in a:
        for b_start, b_end in b:
            start, end = max(a_start, b_start), min(a_end, b_end)
            if end > start:
                out.append((start, end))
    return _union(out)


def _seconds(intervals: Iterable[Interval]) -> float:
    return sum((end - start).total_seconds() for start, end in intervals)


def _minutes(intervals: Iterable[Interval]) -> int:
    return int(_seconds(intervals) // 60)


# ------------------------------------------------------------------------------- helpers


def _order(e: Event) -> tuple:
    return (e.occurred_at, e.recorded_at, str(e.id))


def effective_events(events: Iterable[Event]) -> list[Event]:
    """Drop voided events (and, for a voided session start, the whole session) and the void
    markers themselves; sorted by time."""
    events = list(events)
    voided = {e.voids_event_id for e in events if e.type == "event_voided" and e.voids_event_id}
    dead_sessions = {
        e.id for e in events if e.id in voided and e.type in START_TYPES
    }  # sessions whose start was voided
    kept = [
        e
        for e in events
        if e.type != "event_voided"
        and e.id not in voided
        and not (e.session_id in dead_sessions if e.session_id else False)
    ]
    return sorted(kept, key=_order)


def local_instant(day: date, at: time, tz: ZoneInfo) -> datetime:
    """The UTC instant of wall-clock ``at`` on local ``day``. A time skipped by a DST gap or
    repeated by its end resolves deterministically (zoneinfo, fold=0) instead of failing."""
    return datetime.combine(day, at, tzinfo=tz).astimezone(UTC)


def work_window(
    day: date, office: OfficeInfo, policy: PolicyRules, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    start_t = office.work_start or policy.work_start
    end_t = office.work_end or policy.work_end
    start = local_instant(day, start_t, tz)
    end_day = day if end_t > start_t else day + timedelta(days=1)  # overnight shifts
    return start, local_instant(end_day, end_t, tz)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_non_working_day(day: date, policy: PolicyRules) -> str | None:
    """``"holiday"``, ``"weekend"`` or None."""
    if day in policy.holidays:
        return "holiday"
    if day.weekday() in policy.weekly_off:
        return "weekend"
    return None


# --------------------------------------------------------------------------- the function


def compute_day(
    events: Iterable[Event],
    samples: SamplesSummary,
    policy: PolicyRules,
    office: OfficeInfo,
    day: date,
    *,
    now: datetime,
    on_leave: bool = False,
) -> DayRecord:
    tz = ZoneInfo(office.timezone)
    events = list(events)
    live = effective_events(events)
    grace = timedelta(minutes=policy.heartbeat_grace_minutes)
    flags: set[str] = set()
    if any(e.type in ("interval_added", "event_voided") for e in events):
        flags.add("corrected")

    closes: dict[uuid.UUID, Event] = {}
    presence: dict[uuid.UUID, list[Event]] = {}
    for e in live:
        if e.type in CLOSE_TYPES and e.session_id and e.session_id not in closes:
            closes[e.session_id] = e
        elif e.type == "presence_changed" and e.session_id:
            presence.setdefault(e.session_id, []).append(e)

    work: list[Interval] = []
    away: list[Interval] = []
    added_work: list[Interval] = []
    breaks: list[Interval] = []
    sessions: list[SessionInfo] = []
    starts: list[datetime] = []
    ends: list[datetime] = []
    any_open = False

    for e in live:
        if e.type == "interval_added":
            start, end = _parse_dt(e.payload.get("start")), _parse_dt(e.payload.get("end"))
            if start is None or end is None or end <= start:
                continue
            (breaks if e.payload.get("kind") == "break" else added_work).append((start, end))

    for s in (e for e in live if e.type in START_TYPES):
        close = closes.get(s.id)
        pres = presence.get(s.id, [])
        stale = False
        if close is not None:
            end = max(close.occurred_at, s.occurred_at)
            open_ = False
        else:
            open_ = True
            evidence = max([s.occurred_at, *[p.occurred_at for p in pres]])
            evidence = max(evidence, samples.last_at.get(s.id, evidence))
            cap = s.occurred_at + timedelta(hours=policy.max_session_hours)
            end = min(now, evidence + grace, cap)
            stale = now - evidence > grace or now >= cap
            end = max(end, s.occurred_at)
        # Cut the session into segments by state.
        state, cursor = "present", s.occurred_at
        segments: list[tuple[str, Interval]] = []
        for p in pres:
            if not s.occurred_at < p.occurred_at < end:
                continue
            new_state = p.payload.get("state")
            if new_state not in ("present", "away", "outside"):
                continue
            segments.append((state, (cursor, p.occurred_at)))
            state, cursor = new_state, p.occurred_at
        segments.append((state, (cursor, end)))
        present_iv = [iv for st, iv in segments if st == "present"]
        away_iv = [iv for st, iv in segments if st != "present"]
        mode = "remote" if s.type == "remote_check_in" else "office"
        if mode == "remote":
            flags.add("remote")
        if policy.counting == "span":
            counted = [(s.occurred_at, end)]
        else:  # presence, span_minus_away
            counted = present_iv
        work.extend(counted)
        away.extend(away_iv)
        starts.append(s.occurred_at)
        closed_by = close.type if close else None
        reason = (close.payload.get("reason") if close else None) or None
        if close is not None:
            ends.append(end)
            if close.type == "auto_check_out":
                flags.add("auto_checked_out")
                if reason in CLOSE_REASONS_MISSING:
                    flags.add("missing_check_out")
        else:
            any_open = True
            if stale:
                flags.add("missing_check_out")
            else:
                flags.add("open")
        sessions.append(
            SessionInfo(
                id=s.id,
                mode=mode,
                started_at=s.occurred_at,
                ended_at=end if close is not None else None,
                closed_by=closed_by,
                close_reason=reason,
                worked_minutes=_minutes(_union(counted)),
                away_minutes=_minutes(_union(away_iv)),
                open=open_,
            )
        )

    stale_open = "missing_check_out" in flags and any_open
    work_union = _union([*work, *added_work])
    break_union = _union(breaks)
    break_minutes = _minutes(_intersect(break_union, work_union))
    worked = _minutes(_subtract(work_union, break_union))
    away_union = _subtract(_union(away), _union(added_work))
    away_minutes = _minutes(away_union)

    has_activity = bool(sessions or added_work)
    first_in = min([*starts, *[a[0] for a in added_work]], default=None)
    last_out = None
    if not any_open:
        last_out = max([*ends, *[a[1] for a in added_work]], default=None)

    work_start, work_end = work_window(day, office, policy, tz)
    late = early = 0
    if first_in is not None:
        after = int((first_in - work_start).total_seconds() // 60)
        if after > policy.late_after_minutes:
            late = after
            flags.add("late")
    if last_out is not None:
        before = int((work_end - last_out).total_seconds() // 60)
        if before > policy.early_leave_before_minutes:
            early = before
            flags.add("early_leave")
    overtime = max(0, worked - policy.overtime_after_minutes)
    if overtime:
        flags.add("overtime")

    non_working = is_non_working_day(day, policy)
    if has_activity and non_working:
        flags.add("non_working_day")
    if has_activity and on_leave:
        flags.add("worked_on_leave")

    if has_activity:
        if stale_open:
            status = "incomplete"
        elif any_open and "open" in flags:
            status = "present"  # provisional: still checked in
        elif worked >= policy.full_day_minutes:
            status = "present"
        elif worked >= policy.half_day_minutes and worked > 0:
            status = "half_day"
        else:
            status = "absent"
            flags.add("short_day")
        if on_leave and status in ("absent", "half_day"):
            status = "on_leave"
    elif non_working:  # weekends and holidays inside a leave are not leave days
        status = non_working
    elif on_leave:
        status = "on_leave"
    else:
        status = "absent"

    return DayRecord(
        day=day,
        first_in=first_in,
        last_out=last_out,
        worked_minutes=worked,
        away_minutes=away_minutes,
        break_minutes=break_minutes,
        late_minutes=late,
        early_leave_minutes=early,
        overtime_minutes=overtime,
        status=status,
        flags=sorted(flags),
        sessions=sessions,
        has_activity=has_activity,
    )
