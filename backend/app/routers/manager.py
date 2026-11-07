import csv
import io
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Literal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..config import Settings, get_settings
from ..db import Database, as_utc, get_db, utcnow
from ..face import FaceProvider, FaceUnavailableError
from ..presence import (
    STATUS_ORDER,
    board_status,
    day_status,
    has_beacon,
    is_late,
    last_heartbeat_rssi,
    latest_app_session,
)
from ..schemas import (
    DEFAULT_WORK_START,
    AttendanceReport,
    DailyTotals,
    DashboardOut,
    DecisionIn,
    Headcount,
    LateEntry,
    PresenceRow,
    RequestOut,
    SessionDetailOut,
    UserOut,
)
from ..security import Role, require_manager
from ..services import (
    build_report,
    face_enrolled,
    get_office,
    local_date,
    office_tz,
    parse_object_id,
    request_out,
    session_durations,
    session_minutes,
    session_out,
    user_out,
    validate_range,
)
from .face import get_face_provider

router = APIRouter(prefix="/manager", tags=["manager"])


def _team_filter(manager: dict) -> dict:
    """Admins see everyone; managers see their own team."""
    if manager["role"] == Role.admin:
        return {}
    return {"team_id": manager.get("team_id") or "__none__"}


async def _team_member(db: Database, manager: dict, employee_id: str) -> dict:
    user = await db.users.find_one({"employee_id": employee_id, **_team_filter(manager)})
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found in your team")
    return user


@router.get("/team", response_model=list[UserOut])
async def team(
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    docs = await db.users.find(_team_filter(manager)).sort("employee_id", 1).to_list(length=None)
    return [user_out(d, settings) for d in docs]


# ---------- helpers for the console endpoints (contract v3) ----------
# Each of these is one query for the whole team, never one per employee.


async def _active_members(db: Database, manager: dict, office_id: str | None = None) -> list[dict]:
    query = {**_team_filter(manager), "active": {"$ne": False}}
    if office_id:
        query["office_id"] = office_id
    return await db.users.find(query).sort("employee_id", 1).to_list(length=None)


async def _offices_by_id(db: Database, members: Iterable[dict]) -> dict[str, dict]:
    oids = []
    for office_id in {m.get("office_id") for m in members if m.get("office_id")}:
        try:
            oids.append(ObjectId(office_id))
        except (InvalidId, TypeError):
            continue
    if not oids:
        return {}
    docs = await db.offices.find({"_id": {"$in": oids}}).to_list(length=None)
    return {str(d["_id"]): d for d in docs}


async def _leave_days(
    db: Database, employee_ids: list[str], start: date, end: date
) -> dict[str, set[str]]:
    """employee_id -> ISO dates within [start, end] covered by an approved leave request."""
    if not employee_ids:
        return {}
    requests = await db.attendance_requests.find(
        {
            "employee_id": {"$in": employee_ids},
            "status": "Approved",
            "kind": "leave",
            "start_date": {"$lte": end.isoformat()},
            "end_date": {"$gte": start.isoformat()},
        }
    ).to_list(length=None)
    days: dict[str, set[str]] = defaultdict(set)
    for req in requests:
        day = max(date.fromisoformat(req["start_date"]), start)
        last = min(date.fromisoformat(req["end_date"]), end)
        while day <= last:
            days[req["employee_id"]].add(day.isoformat())
            day += timedelta(days=1)
    return days


async def _beacons_for_members(db: Database, members: list[dict]) -> list[dict]:
    """Active beacons that could matter to these members (assigned to them, or zone beacons of
    their offices), for ``presence.has_beacon``."""
    ids = [m["employee_id"] for m in members]
    office_ids = sorted({m["office_id"] for m in members if m.get("office_id")})
    return await db.beacons.find(
        {
            "active": {"$ne": False},
            "$or": [
                {"assigned_employee_id": {"$in": ids}},
                {"office_id": {"$in": office_ids}, "assigned_employee_id": None},
            ],
        }
    ).to_list(length=None)


@router.get("/presence", response_model=list[PresenceRow])
async def presence_board(
    office_id: str | None = Query(default=None),
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Live board: one row per active team member with their current status."""
    now = utcnow()
    members = await _active_members(db, manager, office_id)
    if not members:
        return []
    offices = await _offices_by_id(db, members)
    today_of = {
        m["employee_id"]: local_date(now, office_tz(offices.get(m.get("office_id"))))
        for m in members
    }
    ids = list(today_of)
    dates = sorted(set(today_of.values()))
    sessions = await db.attendance_sessions.find(
        {
            "employee_id": {"$in": ids},
            "$or": [{"date": {"$in": dates}}, {"ended_at": None, "source": "app"}],
        }
    ).to_list(length=None)
    by_employee: dict[str, list[dict]] = defaultdict(list)
    for doc in sessions:
        by_employee[doc["employee_id"]].append(doc)
    leave = await _leave_days(db, ids, date.fromisoformat(dates[0]), date.fromisoformat(dates[-1]))
    beacons = await _beacons_for_members(db, members)
    grace = settings.heartbeat_grace_minutes * 60

    rows = []
    for member in members:
        eid = member["employee_id"]
        today = today_of[eid]
        mine = by_employee.get(eid, [])
        todays = [s for s in mine if s["date"] == today]
        # An open session counts even if it was started on an earlier day.
        latest = latest_app_session(
            [s for s in mine if s["date"] == today or s.get("ended_at") is None]
        )
        state = board_status(latest, today in leave.get(eid, ()), now, grace)
        checked_in = latest is not None and latest.get("ended_at") is None
        rows.append(
            PresenceRow(
                employee_id=eid,
                name=member["name"],
                office_id=member.get("office_id"),
                status=state,
                checked_in=checked_in,
                session_id=str(latest["_id"]) if checked_in else None,
                since=as_utc(latest["started_at"]) if checked_in else None,
                last_heartbeat_at=(
                    as_utc(latest.get("last_heartbeat_at") or latest["started_at"])
                    if checked_in
                    else None
                ),
                best_rssi=last_heartbeat_rssi(latest) if checked_in else None,
                today_minutes=sum(session_minutes(s, now, settings) for s in todays),
                face_enrolled=face_enrolled(member),
                has_beacon=has_beacon(member, beacons),
            )
        )
    rows.sort(key=lambda r: (STATUS_ORDER.index(r.status), r.name.casefold()))
    return rows


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    day: date | None = Query(default=None, alias="date"),
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    now = utcnow()
    members = await _active_members(db, manager)
    offices = await _offices_by_id(db, members)
    if day is None:
        # "Today" in the time zone of the first office in scope (UTC when there is none).
        first = min(offices.values(), key=lambda o: o["name"], default=None)
        day = date.fromisoformat(local_date(now, office_tz(first)))
    day_iso = day.isoformat()
    first_day = day - timedelta(days=13)
    ids = [m["employee_id"] for m in members]

    sessions = (
        await db.attendance_sessions.find(
            {
                "employee_id": {"$in": ids},
                "$or": [
                    {"date": {"$gte": first_day.isoformat(), "$lte": day_iso}},
                    {"ended_at": None, "source": "app"},
                ],
            }
        ).to_list(length=None)
        if ids
        else []
    )
    # Per employee: sessions of the chosen day, plus any still-open ones (for the live status).
    relevant: dict[str, list[dict]] = defaultdict(list)
    daily = {
        (first_day + timedelta(days=i)).isoformat(): {"worked": 0, "away": 0, "people": set()}
        for i in range(14)
    }
    for doc in sessions:
        if doc["date"] == day_iso or doc.get("ended_at") is None:
            relevant[doc["employee_id"]].append(doc)
        bucket = daily.get(doc["date"])
        if bucket is not None:
            worked, away = session_durations(doc, now, settings)
            bucket["worked"] += worked // 60
            bucket["away"] += away // 60
            bucket["people"].add(doc["employee_id"])

    leave = await _leave_days(db, ids, day, day)
    grace = settings.heartbeat_grace_minutes * 60
    headcount = Headcount(total=len(members))
    late: list[LateEntry] = []
    for member in members:
        eid = member["employee_id"]
        office = offices.get(member.get("office_id"))
        tz = office_tz(office)
        mine = relevant.get(eid, [])
        on_day = [s for s in mine if s["date"] == day_iso]
        on_leave = day_iso in leave.get(eid, ())
        if day_iso == local_date(now, tz):  # live view of today
            state = board_status(latest_app_session(mine), on_leave, now, grace)
        else:
            state = day_status(bool(on_day), on_leave)
        setattr(headcount, state, getattr(headcount, state) + 1)
        if on_day:
            first_in = as_utc(min(on_day, key=lambda s: as_utc(s["started_at"]))["started_at"])
            work_start = (office or {}).get("work_start") or DEFAULT_WORK_START
            if is_late(first_in, work_start, tz):
                late.append(
                    LateEntry(employee_id=eid, name=member["name"], first_check_in=first_in)
                )
    late.sort(key=lambda e: e.first_check_in, reverse=True)  # latest arrivals first

    beacons = await _beacons_for_members(db, members)
    is_admin = manager["role"] == Role.admin
    return DashboardOut(
        date=day_iso,
        headcount=headcount,
        pending_requests=await db.attendance_requests.count_documents(
            {**_team_filter(manager), "status": "Pending"}
        ),
        unassigned_beacons=(
            await db.beacons.count_documents(
                {"assigned_employee_id": None, "active": {"$ne": False}}
            )
            if is_admin
            else None
        ),
        employees_without_face=sum(1 for m in members if not face_enrolled(m)),
        employees_without_beacon=(
            sum(1 for m in members if not has_beacon(m, beacons))
            if settings.require_bluetooth
            else 0
        ),
        daily=[
            DailyTotals(
                date=d, worked_minutes=b["worked"], away_minutes=b["away"], people=len(b["people"])
            )
            for d, b in daily.items()
        ],
        late_today=late[:10],
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def session_detail(
    session_id: str,
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """One session with its check-in details and presence timeline (team scope)."""
    doc = await db.attendance_sessions.find_one({"_id": parse_object_id(session_id, "session")})
    owner = (
        await db.users.find_one({"employee_id": doc["employee_id"], **_team_filter(manager)})
        if doc
        else None
    )
    if not doc or not owner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    log = [
        {**entry, "at": as_utc(entry["at"])}
        for entry in doc.get("presence_log") or []
        if entry.get("at") is not None
    ]
    return SessionDetailOut(
        **session_out(doc, utcnow(), settings).model_dump(),
        employee_id=owner["employee_id"],
        name=owner["name"],
        office_id=doc.get("office_id") or owner.get("office_id"),
        check_in=doc.get("check_in"),
        presence_log=log,
    )


CSV_COLUMNS = [
    "employee_id",
    "name",
    "office",
    "date",
    "first_in",
    "last_out",
    "worked_minutes",
    "away_minutes",
    "sessions",
    "on_leave",
]


def _csv_text(value: str) -> str:
    """Stop spreadsheets from running a name like ``=HYPERLINK(...)`` as a formula."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.get("/attendance/export.csv")
async def export_attendance_csv(
    start: date = Query(),
    end: date = Query(),
    office_id: str | None = Query(default=None),
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """One row per employee per day that has sessions or approved leave. Must stay registered
    before ``/attendance/{employee_id}``, which would otherwise capture ``export.csv``."""
    validate_range(start, end)
    now = utcnow()
    members = await _active_members(db, manager, office_id)
    offices = await _offices_by_id(db, members)
    ids = [m["employee_id"] for m in members]
    sessions = (
        await db.attendance_sessions.find(
            {
                "employee_id": {"$in": ids},
                "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
            }
        ).to_list(length=None)
        if ids
        else []
    )
    leave = await _leave_days(db, ids, start, end)
    by_day: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for doc in sessions:
        by_day[doc["employee_id"]][doc["date"]].append(doc)

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for member in members:
        eid = member["employee_id"]
        office = offices.get(member.get("office_id"))
        tz = office_tz(office)
        for day in sorted(set(by_day.get(eid, {})) | leave.get(eid, set())):
            docs = by_day.get(eid, {}).get(day, [])
            starts = [as_utc(d["started_at"]) for d in docs]
            ends = [as_utc(d["ended_at"]) for d in docs if d.get("ended_at")]
            still_open = any(d.get("ended_at") is None for d in docs)
            durations = [session_durations(d, now, settings) for d in docs]
            writer.writerow(
                [
                    eid,
                    _csv_text(member["name"]),
                    _csv_text((office or {}).get("name", "")),
                    day,
                    min(starts).astimezone(tz).strftime("%H:%M") if starts else "",
                    max(ends).astimezone(tz).strftime("%H:%M") if ends and not still_open else "",
                    sum(w // 60 for w, _ in durations),
                    sum(a // 60 for _, a in durations),
                    len(docs),
                    "true" if day in leave.get(eid, ()) else "false",
                ]
            )
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="attendance_{start}_{end}.csv"'},
    )


@router.get("/attendance", response_model=list[AttendanceReport])
async def team_attendance(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    now = utcnow()
    end = end or now.date()
    start = start or end - timedelta(days=6)
    validate_range(start, end)
    members = (
        await db.users.find({**_team_filter(manager), "active": {"$ne": False}})
        .sort("employee_id", 1)
        .to_list(length=None)
    )
    return [await build_report(db, m, start, end, now, settings) for m in members]


@router.get("/attendance/{employee_id}", response_model=AttendanceReport)
async def employee_attendance(
    employee_id: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    member = await _team_member(db, manager, employee_id)
    now = utcnow()
    end = end or now.date()
    start = start or end - timedelta(days=30)
    validate_range(start, end)
    return await build_report(db, member, start, end, now, settings)


@router.get("/requests", response_model=list[RequestOut])
async def team_requests(
    status_filter: Literal["Pending", "Approved", "Rejected"] | None = Query(
        default=None, alias="status"
    ),
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
):
    query = dict(_team_filter(manager))
    if status_filter:
        query["status"] = status_filter
    docs = await db.attendance_requests.find(query).sort("submitted_at", -1).to_list(length=500)
    ids = list({d["employee_id"] for d in docs})
    users = await db.users.find({"employee_id": {"$in": ids}}).to_list(length=None)
    names = {u["employee_id"]: u["name"] for u in users}
    return [request_out(d, names.get(d["employee_id"])) for d in docs]


@router.post("/requests/{request_id}/decision", response_model=RequestOut)
async def decide_request(
    request_id: str,
    data: DecisionIn,
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
):
    oid = parse_object_id(request_id, "request")
    req = await db.attendance_requests.find_one({"_id": oid, **_team_filter(manager)})
    if not req:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if req["employee_id"] == manager["employee_id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot decide on your own request")
    if req["status"] != "Pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request is already {req['status']}")

    new_status = "Approved" if data.action == "approve" else "Rejected"
    now = utcnow()
    # Only flip from Pending, so two managers clicking at once can't both apply it.
    result = await db.attendance_requests.update_one(
        {"_id": oid, "status": "Pending"},
        {
            "$set": {
                "status": new_status,
                "decided_by": manager["employee_id"],
                "decided_at": now,
                "comment": data.comment,
            }
        },
    )
    if result.modified_count == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Request was already decided")

    if new_status == "Approved" and req["kind"] == "correction":
        employee = await db.users.find_one({"employee_id": req["employee_id"]})
        office = await get_office(db, (employee or {}).get("office_id"))
        tz = office_tz(office)
        day = date.fromisoformat(req["start_date"])
        start = datetime.combine(day, time.fromisoformat(req["entry_time"]), tz)
        end = datetime.combine(day, time.fromisoformat(req["exit_time"]), tz)
        await db.attendance_sessions.insert_one(
            {
                "employee_id": req["employee_id"],
                "team_id": req.get("team_id"),
                "office_id": (employee or {}).get("office_id"),
                "date": day.isoformat(),
                "started_at": start,
                "ended_at": end,
                "source": "request",
                "request_id": oid,
            }
        )

    updated = await db.attendance_requests.find_one({"_id": oid})
    employee = await db.users.find_one({"employee_id": req["employee_id"]})
    return request_out(updated, (employee or {}).get("name"))


@router.delete("/team/{employee_id}/face", status_code=status.HTTP_204_NO_CONTENT)
async def reset_face(
    employee_id: str,
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    provider: FaceProvider = Depends(get_face_provider),
):
    """Clear an employee's enrolled face so they can register it again."""
    member = await _team_member(db, manager, employee_id)
    try:
        fields = await provider.reset(member)
    except FaceUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    unset = {f: "" for f in [*fields, "face_enrolled_at", "face_verified_at"]}
    await db.users.update_one({"employee_id": employee_id}, {"$unset": unset})
