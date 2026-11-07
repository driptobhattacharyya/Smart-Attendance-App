from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import Settings, get_settings
from ..db import Database, as_utc, get_db, utcnow
from ..geo import haversine_m
from ..presence import (
    SessionClosedError,
    apply_presence,
    best_rssi,
    decide_state,
    is_near,
)
from ..retention import maybe_prune
from ..schemas import (
    AttendanceReport,
    CheckInIn,
    CheckOutIn,
    HeartbeatIn,
    HeartbeatOut,
    SessionOut,
    StatusOut,
)
from ..security import get_consenting_user, get_current_user
from ..services import (
    beacons_for_user,
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
        session=session_out(open_doc, now, settings) if open_doc else None,
        face_verified_until=valid_until if valid_until and valid_until > now else None,
        today_minutes=sum(session_minutes(s, now, settings) for s in todays),
        presence=open_doc.get("last_state") if open_doc else None,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
    )


@router.post("/check-in", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def check_in(
    data: CheckInIn,
    user: dict = Depends(get_consenting_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Start an attendance session. The server checks every rule itself:
    a recent face match, being inside the office geofence and (unless REQUIRE_BLUETOOTH is off)
    the employee's beacon being heard with enough signal."""
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

    readings = [r.model_dump() for r in data.beacons]
    mine = await beacons_for_user(db, user)
    best = best_rssi(readings, mine)
    if settings.require_bluetooth:
        if not mine:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "No beacon configured for you; ask your admin"
            )
        if not is_near(best, settings.bluetooth_min_rssi):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Your desk beacon is not nearby" if best is None else "Beacon signal too weak",
            )

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
        "last_heartbeat_at": now,
        "last_state": "present",
        "present_seconds": 0,
        "away_seconds": 0,
        "presence_log": [],
        "check_in": {
            "latitude": data.latitude,
            "longitude": data.longitude,
            "accuracy": data.accuracy,
            "distance_m": round(distance, 1),
            "best_rssi": best,
        },
    }
    result = await db.attendance_sessions.insert_one(doc)
    doc["_id"] = result.inserted_id
    # A face scan is good for one check-in only.
    await db.users.update_one(
        {"employee_id": user["employee_id"]}, {"$unset": {"face_verified_at": ""}}
    )
    await maybe_prune(db, settings, now)  # housekeeping, at most once an hour
    return session_out(doc, now, settings)


@router.post("/heartbeat", response_model=HeartbeatOut)
async def heartbeat(
    data: HeartbeatIn,
    user: dict = Depends(get_consenting_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Sent by the app while checked in. Credits the time since the last heartbeat to the
    previous state (present/away, capped at the grace window) and records the new state.
    Leaving the geofence closes the session."""
    doc = await _open_session(db, user["employee_id"])
    if not doc:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not checked in")
    office = await get_office(db, user.get("office_id"))
    if not office:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not assigned to an office")

    now = utcnow()
    distance = haversine_m(data.latitude, data.longitude, office["latitude"], office["longitude"])
    inside = distance <= office["radius"]
    readings = [r.model_dump() for r in data.beacons]
    best = best_rssi(readings, await beacons_for_user(db, user))
    near = is_near(best, settings.bluetooth_min_rssi)
    state = decide_state(inside, near, settings.require_bluetooth)

    try:
        updated = await apply_presence(
            db,
            doc["_id"],
            now,
            settings.heartbeat_grace_minutes * 60,
            state=state,
            log_entry={
                "at": now,
                "state": state,
                "distance_m": round(distance, 1),
                "best_rssi": best,
            },
            end=state == "outside",
        )
    except SessionClosedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not checked in") from exc
    return HeartbeatOut(
        state=state,
        checked_in=updated.get("ended_at") is None,
        session=session_out(updated, now, settings),
        next_heartbeat_seconds=settings.heartbeat_interval_seconds,
    )


@router.post("/check-out", response_model=SessionOut)
async def check_out(
    data: CheckOutIn | None = None,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Finalise the open session: credit the last interval, then close it."""
    doc = await _open_session(db, user["employee_id"])
    if not doc:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not checked in")
    now = utcnow()
    try:
        updated = await apply_presence(
            db,
            doc["_id"],
            now,
            settings.heartbeat_grace_minutes * 60,
            state=None,
            end=True,
            extra_set={"check_out": data.model_dump() if data else {}},
        )
    except SessionClosedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not checked in") from exc
    return session_out(updated, now, settings)


@router.get("/me", response_model=AttendanceReport)
async def my_attendance(
    start: date | None = Query(default=None, description="Defaults to 30 days ago"),
    end: date | None = Query(default=None, description="Defaults to today"),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    now = utcnow()
    if end is None:  # "today" in the office's time zone, which is how sessions are dated
        office = await get_office(db, user.get("office_id"))
        end = date.fromisoformat(local_date(now, office_tz(office)))
    start = start or end - timedelta(days=30)
    validate_range(start, end)
    return await build_report(db, user, start, end, now, settings)
