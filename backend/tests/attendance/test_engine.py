"""Table-driven tests of the pure calculation engine (no database)."""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.attendance.engine import (
    Event,
    OfficeInfo,
    SamplesSummary,
    compute_day,
    effective_events,
)
from app.attendance.rules import PolicyRules

_counter = itertools.count()


def at(text: str) -> datetime:
    """'2026-03-02 09:00' (UTC) -> aware datetime."""
    return datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=UTC)


def ev(type_, when, session=None, payload=None, voids=None, id_=None) -> Event:
    return Event(
        id=id_ or uuid.uuid4(),
        type=type_,
        occurred_at=at(when),
        recorded_at=at("2026-03-02 00:00") + timedelta(microseconds=next(_counter)),
        session_id=session,
        payload=payload or {},
        voids_event_id=voids,
    )


def session(start, end=None, *, remote=False, reason=None, close="check_out", states=()):
    """Events of one session: [(hh:mm, state)] adds presence_changed events (same date as start)."""
    sid = uuid.uuid4()
    day = start.split(" ")[0]
    out = [ev("remote_check_in" if remote else "check_in", start, id_=sid)]
    for hhmm, state in states:
        out.append(ev("presence_changed", f"{day} {hhmm}", sid, {"state": state}))
    if end:
        out.append(ev(close, end, sid, {"reason": reason} if reason else {}))
    return out, sid


D = date(2026, 3, 2)  # a Monday
NOW = at("2026-03-03 12:00")
UTC_OFFICE = OfficeInfo("UTC")
RULES = PolicyRules()


def run(events, *, rules=RULES, office=UTC_OFFICE, day=D, now=NOW, samples=None, on_leave=False):
    return compute_day(
        events,
        SamplesSummary(samples or {}),
        rules,
        office,
        day,
        now=now,
        on_leave=on_leave,
    )


def rules(**kw) -> PolicyRules:
    return PolicyRules(**kw)


# ------------------------------------------------------------------ basic day, thresholds


@pytest.mark.parametrize(
    ("start", "end", "worked", "status", "late", "early", "overtime", "flags"),
    [
        ("2026-03-02 09:00", "2026-03-02 18:00", 540, "present", 0, 0, 0, set()),
        ("2026-03-02 09:10:59", "2026-03-02 18:00", 529, "present", 0, 0, 0, set()),
        ("2026-03-02 09:11", "2026-03-02 18:00", 529, "present", 11, 0, 0, {"late"}),
        ("2026-03-02 09:25", "2026-03-02 18:00", 515, "present", 25, 0, 0, {"late"}),
        ("2026-03-02 09:00", "2026-03-02 17:45", 525, "present", 0, 0, 0, set()),
        ("2026-03-02 09:00", "2026-03-02 17:44", 524, "present", 0, 16, 0, {"early_leave"}),
        ("2026-03-02 09:00", "2026-03-02 19:30", 630, "present", 0, 0, 90, {"overtime"}),
        ("2026-03-02 09:00", "2026-03-02 16:59", 479, "half_day", 0, 61, 0, {"early_leave"}),
        ("2026-03-02 09:00", "2026-03-02 13:00", 240, "half_day", 0, 300, 0, {"early_leave"}),
        (
            "2026-03-02 09:00",
            "2026-03-02 12:59",
            239,
            "absent",
            0,
            301,
            0,
            {"early_leave", "short_day"},
        ),
        (
            "2026-03-02 09:00",
            "2026-03-02 09:00",
            0,
            "absent",
            0,
            540,
            0,
            {"early_leave", "short_day"},
        ),
    ],
)
def test_thresholds(start, end, worked, status, late, early, overtime, flags):
    events, _ = session(start, end)
    r = run(events)
    assert (r.worked_minutes, r.status, r.late_minutes) == (worked, status, late)
    assert (r.early_leave_minutes, r.overtime_minutes) == (early, overtime)
    assert set(r.flags) == flags
    assert r.first_in == at(start)
    assert r.has_activity


def test_seconds_are_floored_per_total_not_per_session():
    a, _ = session("2026-03-02 09:00:00", "2026-03-02 09:00:40")
    b, _ = session("2026-03-02 10:00:00", "2026-03-02 10:00:40")
    # 40 s + 40 s = 80 s = 1 minute (flooring each would give 0)
    assert run(a + b).worked_minutes == 1


def test_no_events_statuses():
    assert run([]).status == "absent"
    assert run([], day=date(2026, 3, 7)).status == "weekend"  # Saturday
    assert run([], day=date(2026, 3, 8)).status == "weekend"
    assert run([], rules=rules(holidays=[D])).status == "holiday"
    assert run([], on_leave=True).status == "on_leave"
    assert run([], day=date(2026, 3, 7), on_leave=True).status == "weekend"
    assert run([], rules=rules(holidays=[D]), on_leave=True).status == "holiday"
    assert run([], rules=rules(weekly_off=[0])).status == "weekend"
    assert not run([]).has_activity


def test_work_on_weekend_and_leave_gets_flags():
    events, _ = session("2026-03-07 09:00", "2026-03-07 18:00")
    r = run(events, day=date(2026, 3, 7))
    assert r.status == "present" and "non_working_day" in r.flags
    events, _ = session("2026-03-02 09:00", "2026-03-02 11:00")
    r = run(events, on_leave=True)
    assert r.status == "on_leave" and "worked_on_leave" in r.flags
    events, _ = session("2026-03-02 09:00", "2026-03-02 18:00")
    r = run(events, on_leave=True)
    assert r.status == "present" and "worked_on_leave" in r.flags


# ----------------------------------------------------------------------- counting modes


STATES = [("12:00", "away"), ("13:00", "present")]


@pytest.mark.parametrize(
    ("counting", "worked", "away"),
    [("presence", 480, 60), ("span", 540, 60), ("span_minus_away", 480, 60)],
)
def test_counting_modes_with_an_away_hour(counting, worked, away):
    events, _ = session("2026-03-02 09:00", "2026-03-02 18:00", states=STATES)
    r = run(events, rules=rules(counting=counting))
    assert (r.worked_minutes, r.away_minutes) == (worked, away)
    assert r.sessions[0].worked_minutes == worked and r.sessions[0].away_minutes == away


def test_outside_segment_is_not_worked_but_counts_in_span():
    events, _ = session(
        "2026-03-02 09:00", "2026-03-02 13:00", states=[("10:00", "outside"), ("11:00", "present")]
    )
    assert run(events).worked_minutes == 180
    assert run(events, rules=rules(counting="span")).worked_minutes == 240
    assert run(events, rules=rules(counting="span_minus_away")).worked_minutes == 180
    assert run(events).away_minutes == 60


def test_presence_changed_outside_session_window_and_unknown_states_ignored():
    sid = uuid.uuid4()
    events = [
        ev("presence_changed", "2026-03-02 08:00", sid, {"state": "away"}),
        ev("check_in", "2026-03-02 09:00", id_=sid),
        ev("presence_changed", "2026-03-02 10:00", sid, {"state": "banana"}),
        ev("check_out", "2026-03-02 11:00", sid),
        ev("presence_changed", "2026-03-02 12:00", sid, {"state": "away"}),
    ]
    r = run(events)
    assert r.worked_minutes == 120 and r.away_minutes == 0


def test_two_sessions_in_a_day_and_input_order_does_not_matter():
    a, _ = session("2026-03-02 09:00", "2026-03-02 12:00")
    b, _ = session("2026-03-02 13:00", "2026-03-02 18:00")
    shuffled = list(reversed(a + b))
    r = run(shuffled)
    assert r.worked_minutes == 480 and r.status == "present"
    assert r.first_in == at("2026-03-02 09:00") and r.last_out == at("2026-03-02 18:00")
    assert r.session_count == 2 and [s.worked_minutes for s in r.sessions] == [180, 300]


def test_remote_session_flag_and_mode():
    events, _ = session("2026-03-02 09:00", "2026-03-02 18:00", remote=True)
    r = run(events)
    assert "remote" in r.flags and r.sessions[0].mode == "remote"


# ------------------------------------------------------------------------------ midnight


def test_session_crossing_midnight_belongs_to_the_checkin_day():
    sid = uuid.uuid4()
    events = [
        ev("check_in", "2026-03-02 22:00", id_=sid),
        ev("presence_changed", "2026-03-03 00:30", sid, {"state": "away"}),
        ev("presence_changed", "2026-03-03 01:00", sid, {"state": "present"}),
        ev("check_out", "2026-03-03 02:00", sid),
    ]
    r = run(events, rules=rules(max_session_hours=24))
    assert r.worked_minutes == 210 and r.away_minutes == 30
    assert r.first_in == at("2026-03-02 22:00") and r.last_out == at("2026-03-03 02:00")
    # the next day has no events of its own
    assert run([], day=date(2026, 3, 3)).status == "absent"


def test_overnight_shift_window_ends_next_day():
    rl = rules(work_start=time(22, 0), work_end=time(6, 0), early_leave_before_minutes=0)
    events, _ = session("2026-03-02 22:00", "2026-03-03 05:00")
    r = run(events, rules=rl)
    assert r.late_minutes == 0 and r.early_leave_minutes == 60


# ------------------------------------------------------------------------ time zones / DST


def test_office_timezone_drives_late_and_early():
    kolkata = OfficeInfo("Asia/Kolkata", time(9, 30), time(18, 30))
    # 09:45 IST = 04:15Z -> 15 min after start -> late; leaves 18:00 IST -> 30 min early
    events, _ = session("2026-03-02 04:15", "2026-03-02 12:30")
    r = run(events, office=kolkata)
    assert r.late_minutes == 15 and r.early_leave_minutes == 30


def test_office_hours_override_policy_defaults():
    office = OfficeInfo("UTC", time(10, 0), time(19, 0))
    events, _ = session("2026-03-02 10:05", "2026-03-02 19:00")
    r = run(events, office=office)
    assert r.late_minutes == 0 and r.early_leave_minutes == 0
    assert run(events).late_minutes == 65  # policy default 09:00


@pytest.mark.parametrize(
    ("tz", "day", "checkin_utc", "late"),
    [
        # Berlin spring forward 2026-03-29: 09:00 CEST = 07:00Z
        ("Europe/Berlin", date(2026, 3, 29), "2026-03-29 07:05", 0),
        ("Europe/Berlin", date(2026, 3, 29), "2026-03-29 07:30", 30),
        ("Europe/Berlin", date(2026, 3, 29), "2026-03-29 08:05", 65),  # 09:05 CET would be wrong
        # day before: CET, 09:00 = 08:00Z
        ("Europe/Berlin", date(2026, 3, 28), "2026-03-28 08:30", 30),
        # Berlin fall back 2026-10-25: 09:00 CET = 08:00Z
        ("Europe/Berlin", date(2026, 10, 25), "2026-10-25 08:30", 30),
        ("Europe/Berlin", date(2026, 10, 24), "2026-10-24 07:30", 30),  # still CEST: 07:00Z
        # New York spring forward 2026-03-08: 09:00 EDT = 13:00Z
        ("America/New_York", date(2026, 3, 8), "2026-03-08 13:30", 30),
        # Sydney (southern hemisphere) fall back 2026-04-05: 09:00 AEST = 23:00Z previous day
        ("Australia/Sydney", date(2026, 4, 5), "2026-04-04 23:30", 30),
    ],
)
def test_dst_days_use_the_real_offset(tz, day, checkin_utc, late):
    events, _ = session(checkin_utc, checkin_utc.split(" ")[0] + " 23:59")
    r = run(events, office=OfficeInfo(tz), day=day, now=at("2026-12-01 00:00"))
    assert r.late_minutes == late


def test_work_start_inside_a_dst_gap_does_not_crash():
    # 02:30 does not exist in New York on 2026-03-08; zoneinfo resolves it deterministically.
    office = OfficeInfo("America/New_York", time(2, 30), time(11, 0))
    events, _ = session("2026-03-08 08:00", "2026-03-08 16:00")
    r = run(events, office=office, day=date(2026, 3, 8))
    assert r.worked_minutes == 480 and r.late_minutes > 0


def test_worked_time_across_the_dst_gap_is_elapsed_time():
    # 01:00 CET (00:00Z) to 04:00 CEST (02:00Z) on 2026-03-29 is two real hours.
    events, _ = session("2026-03-29 00:00", "2026-03-29 02:00")
    r = run(events, office=OfficeInfo("Europe/Berlin"), day=date(2026, 3, 29))
    assert r.worked_minutes == 120


def test_worked_time_across_the_fall_back_repeat_hour():
    # 01:30 CEST (23:30Z) to 03:30 CET (02:30Z) on 2026-10-25 is three real hours.
    events, _ = session("2026-10-24 23:30", "2026-10-25 02:30")
    r = run(events, office=OfficeInfo("Europe/Berlin"), day=date(2026, 10, 25), rules=rules())
    assert r.worked_minutes == 180


# ------------------------------------------------------------------ open / stale sessions


def test_open_session_is_provisional_and_capped_by_now():
    events, _ = session("2026-03-02 09:00")
    r = run(events, now=at("2026-03-02 09:10"))
    assert r.status == "present" and "open" in r.flags and r.worked_minutes == 10
    assert r.last_out is None and r.sessions[0].open


def test_open_session_credit_stops_at_last_evidence_plus_grace():
    events, sid = session("2026-03-02 09:00")
    r = run(events, now=at("2026-03-02 12:00"), samples={sid: at("2026-03-02 11:50")})
    # evidence 11:50 + 15 grace = 12:05 > now, so credited up to now and not stale
    assert r.worked_minutes == 180 and "open" in r.flags and r.status == "present"


def test_stale_open_session_is_incomplete_with_missing_check_out():
    events, sid = session("2026-03-02 09:00")
    r = run(events, now=at("2026-03-02 15:00"), samples={sid: at("2026-03-02 10:00")})
    assert r.status == "incomplete" and "missing_check_out" in r.flags
    assert r.worked_minutes == 75  # 09:00 .. 10:00 + 15 min grace
    assert r.last_out is None


def test_session_older_than_max_hours_is_capped_and_incomplete():
    events, sid = session("2026-03-02 06:00")
    r = run(events, now=at("2026-03-03 06:00"), samples={sid: at("2026-03-03 05:59")})
    assert r.status == "incomplete"
    assert r.worked_minutes == 14 * 60  # capped at max_session_hours


def test_open_presence_change_extends_evidence():
    sid = uuid.uuid4()
    events = [
        ev("check_in", "2026-03-02 09:00", id_=sid),
        ev("presence_changed", "2026-03-02 09:50", sid, {"state": "away"}),
    ]
    r = run(events, now=at("2026-03-02 10:00"))
    assert (r.worked_minutes, r.away_minutes) == (50, 10) and "open" in r.flags


@pytest.mark.parametrize(
    ("reason", "flags"),
    [
        ("outside", {"auto_checked_out"}),
        ("stale", {"auto_checked_out", "missing_check_out"}),
        ("max_session", {"auto_checked_out", "missing_check_out"}),
        (None, {"auto_checked_out"}),
    ],
)
def test_auto_check_out_flags(reason, flags):
    events, _ = session(
        "2026-03-02 09:00", "2026-03-02 17:00", close="auto_check_out", reason=reason
    )
    r = run(events)
    assert flags <= set(r.flags) and r.status == "present" and r.worked_minutes == 480
    assert r.sessions[0].closed_by == "auto_check_out" and r.sessions[0].close_reason == reason


# ------------------------------------------------------------------------- corrections


def interval(start, end, kind="work"):
    return ev(
        "interval_added",
        start,
        payload={
            "start": f"{start}Z".replace(" ", "T"),
            "end": f"{end}Z".replace(" ", "T"),
            "kind": kind,
        },
    )


def test_correction_adds_a_work_interval_to_an_empty_day():
    r = run([interval("2026-03-02 09:00", "2026-03-02 17:00")])
    assert r.worked_minutes == 480 and r.status == "present" and "corrected" in r.flags
    assert r.first_in == at("2026-03-02 09:00") and r.last_out == at("2026-03-02 17:00")


def test_correction_merges_with_an_overlapping_session():
    events, _ = session("2026-03-02 09:00", "2026-03-02 12:00")
    r = run([*events, interval("2026-03-02 11:00", "2026-03-02 15:00")])
    assert r.worked_minutes == 360 and r.last_out == at("2026-03-02 15:00")


def test_break_interval_reduces_worked_and_is_reported():
    events, _ = session("2026-03-02 09:00", "2026-03-02 18:00")
    r = run([*events, interval("2026-03-02 12:00", "2026-03-02 13:00", "break")])
    assert r.worked_minutes == 480 and r.break_minutes == 60


def test_break_outside_worked_time_is_ignored():
    events, _ = session("2026-03-02 09:00", "2026-03-02 12:00")
    r = run([*events, interval("2026-03-02 13:00", "2026-03-02 14:00", "break")])
    assert r.worked_minutes == 180 and r.break_minutes == 0


def test_malformed_or_inverted_interval_is_ignored():
    bad = [
        ev("interval_added", "2026-03-02 09:00", payload={"start": "x", "end": "y"}),
        interval("2026-03-02 12:00", "2026-03-02 11:00"),
    ]
    r = run(bad)
    assert not r.has_activity and r.status == "absent"


def test_voiding_a_check_out_reopens_the_session():
    events, sid = session("2026-03-02 09:00", "2026-03-02 17:00")
    out = events[-1]
    r = run(
        [*events, ev("event_voided", "2026-03-02 17:30", voids=out.id)], now=at("2026-03-02 17:00")
    )
    # no evidence after 09:00: the reopened session is stale, hence incomplete
    assert "corrected" in r.flags and r.status == "incomplete" and r.last_out is None
    assert "missing_check_out" in r.flags


def test_voiding_a_check_in_removes_the_whole_session():
    events, sid = session("2026-03-02 09:00", "2026-03-02 17:00", states=[("12:00", "away")])
    r = run([*events, ev("event_voided", "2026-03-02 18:00", voids=sid)])
    assert not r.has_activity and r.status == "absent" and r.sessions == []
    assert "corrected" in r.flags


def test_voiding_an_interval_and_a_presence_change():
    events, sid = session("2026-03-02 09:00", "2026-03-02 18:00", states=STATES)
    away = next(e for e in events if e.type == "presence_changed" and e.payload["state"] == "away")
    added = interval("2026-03-02 05:00", "2026-03-02 06:00")
    r = run(
        [
            *events,
            added,
            ev("event_voided", "2026-03-02 19:00", voids=away.id),
            ev("event_voided", "2026-03-02 19:00", voids=added.id),
        ]
    )
    # with the away change voided the session is present the whole time
    assert r.worked_minutes == 540 - 0 and r.away_minutes == 0
    assert r.first_in == at("2026-03-02 09:00")


def test_effective_events_drops_voids_and_dead_sessions():
    events, sid = session("2026-03-02 09:00", "2026-03-02 17:00")
    out = effective_events([*events, ev("event_voided", "2026-03-02 18:00", voids=sid)])
    assert out == []


# --------------------------------------------------------------- policy sensitivity


def test_custom_thresholds():
    rl = rules(
        full_day_minutes=420, half_day_minutes=200, late_after_minutes=0, overtime_after_minutes=420
    )
    events, _ = session("2026-03-02 09:01", "2026-03-02 16:30")
    r = run(events, rules=rl)
    assert r.late_minutes == 1 and r.status == "present" and r.overtime_minutes == 29


def test_half_day_zero_threshold_still_needs_work_for_half_day():
    rl = rules(half_day_minutes=0)
    events, _ = session("2026-03-02 09:00", "2026-03-02 09:00")
    assert run(events, rules=rl).status == "absent"


def test_policy_rules_validation():
    with pytest.raises(ValueError):
        PolicyRules(half_day_minutes=500, full_day_minutes=400)
    with pytest.raises(ValueError):
        PolicyRules(weekly_off=[7])
    assert PolicyRules.model_validate({"unknown": 1, "counting": "span"}).counting == "span"
    assert PolicyRules.model_validate(PolicyRules().to_json()) == PolicyRules()
