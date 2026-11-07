"""Shared helpers: document -> schema conversion and attendance calculations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status

from .db import Database, as_utc
from .schemas import AttendanceReport, DaySummary, Office, RequestOut, SessionOut, UserOut

MAX_REPORT_DAYS = 93


def parse_object_id(value: str, what: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown {what}") from exc


def office_out(doc: dict) -> Office:
    return Office(
        id=str(doc["_id"]),
        name=doc["name"],
        latitude=doc["latitude"],
        longitude=doc["longitude"],
        radius=doc["radius"],
        timezone=doc.get("timezone", "UTC"),
        address=doc.get("address"),
    )


def user_out(doc: dict) -> UserOut:
    return UserOut(
        employee_id=doc["employee_id"],
        name=doc["name"],
        email=doc["email"],
        phone=doc.get("phone"),
        role=doc["role"],
        team_id=doc.get("team_id"),
        office_id=doc.get("office_id"),
        bluetooth_device_id=doc.get("bluetooth_device_id"),
        face_enrolled=bool(doc.get("face_embedding")),
        active=doc.get("active", True),
    )


def request_out(doc: dict, employee_name: str | None = None) -> RequestOut:
    return RequestOut(
        id=str(doc["_id"]),
        employee_id=doc["employee_id"],
        employee_name=employee_name,
        reason=doc["reason"],
        start_date=date.fromisoformat(doc["start_date"]),
        end_date=date.fromisoformat(doc["end_date"]),
        entry_time=doc.get("entry_time"),
        exit_time=doc.get("exit_time"),
        kind=doc["kind"],
        status=doc["status"],
        submitted_at=as_utc(doc["submitted_at"]),
        decided_by=doc.get("decided_by"),
        decided_at=as_utc(doc["decided_at"]) if doc.get("decided_at") else None,
        comment=doc.get("comment"),
    )


async def get_office(db: Database, office_id: str | None) -> dict | None:
    if not office_id:
        return None
    try:
        oid = ObjectId(office_id)
    except (InvalidId, TypeError):
        return None
    return await db.offices.find_one({"_id": oid})


def office_tz(office: dict | None) -> ZoneInfo:
    return ZoneInfo((office or {}).get("timezone") or "UTC")


def local_date(moment: datetime, tz: ZoneInfo) -> str:
    return as_utc(moment).astimezone(tz).date().isoformat()


def session_minutes(doc: dict, now: datetime, max_hours: int) -> int:
    start = as_utc(doc["started_at"])
    if doc.get("ended_at"):
        end = as_utc(doc["ended_at"])
    else:
        end = min(now, start + timedelta(hours=max_hours))
    return max(0, int((end - start).total_seconds() // 60))


def session_out(doc: dict, now: datetime, max_hours: int) -> SessionOut:
    return SessionOut(
        id=str(doc["_id"]),
        date=doc["date"],
        started_at=as_utc(doc["started_at"]),
        ended_at=as_utc(doc["ended_at"]) if doc.get("ended_at") else None,
        source=doc.get("source", "app"),
        minutes=session_minutes(doc, now, max_hours),
    )


def validate_range(start: date, end: date) -> None:
    if end < start:
        raise HTTPException(422, "end must not be before start")
    if (end - start).days >= MAX_REPORT_DAYS:
        raise HTTPException(422, f"Range is limited to {MAX_REPORT_DAYS} days")


async def build_report(
    db: Database, user: dict, start: date, end: date, now: datetime, max_hours: int
) -> AttendanceReport:
    employee_id = user["employee_id"]
    sessions = (
        await db.attendance_sessions.find(
            {
                "employee_id": employee_id,
                "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
            }
        )
        .sort("started_at", 1)
        .to_list(length=None)
    )

    leave_requests = await db.attendance_requests.find(
        {
            "employee_id": employee_id,
            "status": "Approved",
            "kind": "leave",
            "start_date": {"$lte": end.isoformat()},
            "end_date": {"$gte": start.isoformat()},
        }
    ).to_list(length=None)

    by_day: dict[str, list[SessionOut]] = defaultdict(list)
    for doc in sessions:
        by_day[doc["date"]].append(session_out(doc, now, max_hours))

    leave_days: set[str] = set()
    for req in leave_requests:
        day = max(date.fromisoformat(req["start_date"]), start)
        last = min(date.fromisoformat(req["end_date"]), end)
        while day <= last:
            leave_days.add(day.isoformat())
            day += timedelta(days=1)

    days = [
        DaySummary(
            date=d,
            total_minutes=sum(s.minutes for s in by_day.get(d, [])),
            sessions=by_day.get(d, []),
            on_leave=d in leave_days,
        )
        for d in sorted(set(by_day) | leave_days)
    ]
    return AttendanceReport(
        employee_id=employee_id,
        name=user["name"],
        start=start,
        end=end,
        total_minutes=sum(d.total_minutes for d in days),
        days=days,
    )
