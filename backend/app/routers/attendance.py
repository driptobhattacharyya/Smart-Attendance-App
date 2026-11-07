from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import Settings, get_settings
from ..db import Database, as_utc, get_db, utcnow
from ..geo import haversine_m
from ..schemas import AttendanceReport, CheckInIn, CheckOutIn, SessionOut, StatusOut
from ..security import get_current_user
from ..services import (
    build_report,
    get_office,
    local_date,
    office_tz,
    session_minutes,
    session_out,
    validate_range,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])


async def _open_session(db: Database, employee_id: str) -> dict | None:
    return await db.attendance_sessions.find_one(
        {"employee_id": employee_id, "ended_at": None, "source": "app"}
    )


def _face_valid_until(user: dict, settings: Settings):
    verified_at = user.get("face_verified_at")
    if not verified_at:
        return None
    return as_utc(verified_at) + timedelta(minutes=settings.face_verification_ttl_minutes)


@router.get("/status", response_model=StatusOut)
async def attendance_status(
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    now = utcnow()
    office = await get_office(db, user.get("office_id"))
    today = local_date(now, office_tz(office))
    todays = await db.attendance_sessions.find(
        {"employee_id": user["employee_id"], "date": today}
    ).to_list(length=None)
    open_doc = await _open_session(db, user["employee_id"])
    valid_until = _face_valid_until(user, settings)
    return StatusOut(
        checked_in=open_doc is not None,
        session=session_out(open_doc, now, settings.max_session_hours) if open_doc else None,
        face_verified_until=valid_until if valid_until and valid_until > now else None,
        today_minutes=sum(session_minutes(s, now, settings.max_session_hours) for s in todays),
    )


@router.post("/check-in", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def check_in(
    data: CheckInIn,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Start an attendance session. The server checks every rule itself:
    a recent face match, being inside the office geofence and (optionally) the
    registered Bluetooth device being nearby."""
    now = utcnow()
    office = await get_office(db, user.get("office_id"))
    if not office:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not assigned to an office")

    valid_until = _face_valid_until(user, settings)
    if not valid_until or valid_until < now:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Please verify your face first")

    distance = haversine_m(data.latitude, data.longitude, office["latitude"], office["longitude"])
    if distance > office["radius"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"You are {int(distance)} m from {office['name']}; "
            f"check-in is allowed within {int(office['radius'])} m",
        )

    if settings.require_bluetooth:
        registered = user.get("bluetooth_device_id")
        if (
            not registered
            or data.bluetooth_device_id != registered
            or data.bluetooth_rssi is None
            or data.bluetooth_rssi < settings.bluetooth_min_rssi
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your registered device is not nearby")

    if await _open_session(db, user["employee_id"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "You are already checked in")

    doc = {
        "employee_id": user["employee_id"],
        "team_id": user.get("team_id"),
        "office_id": user.get("office_id"),
        "date": local_date(now, office_tz(office)),
        "started_at": now,
        "ended_at": None,
        "source": "app",
        "check_in": {
            "latitude": data.latitude,
            "longitude": data.longitude,
            "accuracy": data.accuracy,
            "distance_m": round(distance, 1),
            "bluetooth_device_id": data.bluetooth_device_id,
            "bluetooth_rssi": data.bluetooth_rssi,
        },
    }
    result = await db.attendance_sessions.insert_one(doc)
    doc["_id"] = result.inserted_id
    # A face scan is good for one check-in only.
    await db.users.update_one(
        {"employee_id": user["employee_id"]}, {"$unset": {"face_verified_at": ""}}
    )
    return session_out(doc, now, settings.max_session_hours)


@router.post("/check-out", response_model=SessionOut)
async def check_out(
    data: CheckOutIn | None = None,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    doc = await _open_session(db, user["employee_id"])
    if not doc:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not checked in")
    now = utcnow()
    check_out_info = data.model_dump() if data else {}
    await db.attendance_sessions.update_one(
        {"_id": doc["_id"]}, {"$set": {"ended_at": now, "check_out": check_out_info}}
    )
    doc["ended_at"] = now
    return session_out(doc, now, settings.max_session_hours)


@router.get("/me", response_model=AttendanceReport)
async def my_attendance(
    start: date | None = Query(default=None, description="Defaults to 30 days ago"),
    end: date | None = Query(default=None, description="Defaults to today"),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    now = utcnow()
    end = end or now.date()
    start = start or end - timedelta(days=30)
    validate_range(start, end)
    return await build_report(db, user, start, end, now, settings.max_session_hours)
