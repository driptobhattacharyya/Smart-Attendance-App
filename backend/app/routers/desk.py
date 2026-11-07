"""Endpoints used by the Attendify Desk app (docs/api-contract-v4-desk-agent.md)."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings, get_settings
from ..db import Database, as_utc, get_db, utcnow
from ..desk import (
    get_desk_beacon,
    hash_secret,
    new_device_token,
    normalize_code,
    secrets_equal,
    unauthorized,
)
from ..schemas import (
    DeskBeaconIdentity,
    DeskEmployee,
    DeskHeartbeatIn,
    DeskHeartbeatOut,
    DeskPairBeacon,
    DeskPairIn,
    DeskPairOut,
)

router = APIRouter(prefix="/desk", tags=["desk"])

INVALID_CODE = "Invalid or expired code"


@router.post("/pair", response_model=DeskPairOut)
async def pair(
    data: DeskPairIn,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Public. Trade a one-time pairing code for a device token (replaces any earlier one)."""
    now = utcnow()
    code_hash = hash_secret(normalize_code(data.code))
    beacon = await db.beacons.find_one({"pairing.code_hash": code_hash})
    pairing = (beacon or {}).get("pairing") or {}
    if (
        not beacon
        or not secrets_equal(pairing.get("code_hash", ""), code_hash)
        or as_utc(pairing["expires_at"]) <= now
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID_CODE)

    token = new_device_token()
    # Single use: only the request that still sees this exact code consumes it.
    result = await db.beacons.update_one(
        {"_id": beacon["_id"], "pairing.code_hash": code_hash},
        {
            "$set": {
                "device_token_hash": hash_secret(token),
                "pairing": None,
                "device": {
                    "hostname": data.hostname,
                    "os": data.os,
                    "agent_version": data.agent_version,
                    "paired_at": now,
                    "last_seen_at": now,
                    "locked": False,
                    "idle_seconds": 0,
                    "advertising": False,
                },
            }
        },
    )
    if result.matched_count != 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID_CODE)

    employee = None
    if beacon.get("assigned_employee_id"):
        user = await db.users.find_one({"employee_id": beacon["assigned_employee_id"]})
        if user:
            employee = DeskEmployee(employee_id=user["employee_id"], name=user["name"])
    return DeskPairOut(
        device_token=token,
        beacon=DeskPairBeacon(
            id=str(beacon["_id"]),
            name=beacon["name"],
            uuid=beacon["uuid"],
            major=beacon.get("major"),
            minor=beacon.get("minor"),
        ),
        employee=employee,
        heartbeat_interval_seconds=settings.desk_heartbeat_interval_seconds,
    )


@router.post("/heartbeat", response_model=DeskHeartbeatOut)
async def desk_heartbeat(
    data: DeskHeartbeatIn,
    beacon: dict = Depends(get_desk_beacon),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Device auth. Records the computer's state; the reply carries the beacon identity so the
    app follows changes an admin made."""
    changes = {
        "device.last_seen_at": utcnow(),
        "device.locked": data.locked,
        "device.idle_seconds": data.idle_seconds,
        "device.advertising": data.advertising,
    }
    if data.agent_version:
        changes["device.agent_version"] = data.agent_version
    # Guard on the token hash: a concurrent unpair/re-pair must not be resurrected.
    result = await db.beacons.update_one(
        {"_id": beacon["_id"], "device_token_hash": beacon["device_token_hash"]},
        {"$set": changes},
    )
    if result.matched_count != 1:
        raise unauthorized()
    return DeskHeartbeatOut(
        beacon=DeskBeaconIdentity(
            uuid=beacon["uuid"], major=beacon.get("major"), minor=beacon.get("minor")
        ),
        heartbeat_interval_seconds=settings.desk_heartbeat_interval_seconds,
        paired=True,
    )
