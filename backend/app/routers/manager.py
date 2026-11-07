from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import Settings, get_settings
from ..db import Database, get_db, utcnow
from ..face import FaceProvider, FaceUnavailableError
from ..schemas import AttendanceReport, DecisionIn, RequestOut, UserOut
from ..security import Role, require_manager
from ..services import (
    build_report,
    get_office,
    office_tz,
    parse_object_id,
    request_out,
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
async def team(manager: dict = Depends(require_manager), db: Database = Depends(get_db)):
    docs = await db.users.find(_team_filter(manager)).sort("employee_id", 1).to_list(length=None)
    return [user_out(d) for d in docs]


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
