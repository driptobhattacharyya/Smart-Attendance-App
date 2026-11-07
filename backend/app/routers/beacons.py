from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.errors import DuplicateKeyError

from ..config import Settings, get_settings
from ..db import Database, get_db, utcnow
from ..desk import format_code, generate_code, hash_secret, insert_desk_beacon
from ..schemas import BeaconIn, BeaconOut, BeaconUpdate, PairingCodeOut
from ..security import Role, get_current_user, require_admin, require_manager
from ..services import beacon_out, beacons_for_user, get_office, parse_object_id

router = APIRouter(prefix="/beacons", tags=["beacons"])


async def _check_refs(db: Database, changes: dict) -> None:
    if changes.get("office_id") and not await get_office(db, changes["office_id"]):
        raise HTTPException(422, "Unknown office")
    if changes.get("assigned_employee_id"):
        employee = await db.users.find_one({"employee_id": changes["assigned_employee_id"]})
        if not employee:
            raise HTTPException(422, "Unknown employee")


@router.get("", response_model=list[BeaconOut])
async def list_beacons(
    office_id: str | None = Query(default=None),
    manager: dict = Depends(require_manager),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Admins see every beacon; managers only those of the offices their team works in."""
    allowed: set[str] | None = None  # None = every office
    if manager["role"] != Role.admin:
        members = await db.users.find({"team_id": manager.get("team_id") or "__none__"}).to_list(
            length=None
        )
        allowed = {m["office_id"] for m in members if m.get("office_id")}
    query: dict = {}
    if office_id:
        if allowed is not None and office_id not in allowed:
            return []
        query["office_id"] = office_id
    elif allowed is not None:
        query["office_id"] = {"$in": sorted(allowed)}
    docs = await db.beacons.find(query).sort("name", 1).to_list(length=None)
    now = utcnow()
    return [beacon_out(d, settings, now) for d in docs]


@router.get("/mine", response_model=list[BeaconOut])
async def my_beacons(
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """The beacons this phone should scan for."""
    now = utcnow()
    return [beacon_out(d, settings, now) for d in await beacons_for_user(db, user)]


@router.post(
    "",
    response_model=BeaconOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_beacon(
    data: BeaconIn, db: Database = Depends(get_db), settings: Settings = Depends(get_settings)
):
    doc = data.model_dump()
    await _check_refs(db, doc)
    if data.kind == "desk_app":
        doc["uuid"] = doc["uuid"] or settings.desk_beacon_uuid.strip().lower()
        auto = doc["major"] is None and doc["minor"] is None
        if not auto and (doc["major"] is None or doc["minor"] is None):
            raise HTTPException(422, "Give both major and minor, or neither")
        doc.update(device=None, device_token_hash=None, pairing=None)
        doc = await insert_desk_beacon(db, doc, auto_identity=auto)
    else:
        result = await db.beacons.insert_one(doc)
        doc["_id"] = result.inserted_id
    return beacon_out(doc, settings)


@router.patch("/{beacon_id}", response_model=BeaconOut, dependencies=[Depends(require_admin)])
async def update_beacon(
    beacon_id: str,
    data: BeaconUpdate,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    oid = parse_object_id(beacon_id, "beacon")
    changes = data.model_dump(exclude_unset=True)
    await _check_refs(db, changes)
    if changes:
        existing = await db.beacons.find_one({"_id": oid})
        if (
            existing
            and existing.get("kind") == "desk_app"
            and (changes.get("major", 0) is None or changes.get("minor", 0) is None)
        ):
            raise HTTPException(422, "A desk app needs a major and a minor")
        try:
            await db.beacons.update_one({"_id": oid}, {"$set": changes})
        except DuplicateKeyError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "Identity already in use") from exc
    doc = await db.beacons.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown beacon")
    return beacon_out(doc, settings)


@router.delete(
    "/{beacon_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)]
)
async def delete_beacon(beacon_id: str, db: Database = Depends(get_db)):
    result = await db.beacons.delete_one({"_id": parse_object_id(beacon_id, "beacon")})
    if result.deleted_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown beacon")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _desk_beacon(db: Database, beacon_id: str) -> dict:
    doc = await db.beacons.find_one({"_id": parse_object_id(beacon_id, "beacon")})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown beacon")
    if doc.get("kind", "tag") != "desk_app":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only desk app beacons can be paired")
    return doc


@router.post(
    "/{beacon_id}/pairing-code",
    response_model=PairingCodeOut,
    dependencies=[Depends(require_admin)],
)
async def create_pairing_code(
    beacon_id: str, db: Database = Depends(get_db), settings: Settings = Depends(get_settings)
):
    """A fresh one-time code for the Attendify Desk app; shown once, replaces any earlier code."""
    doc = await _desk_beacon(db, beacon_id)
    code = generate_code()
    expires_at = utcnow() + timedelta(minutes=settings.desk_pairing_ttl_minutes)
    await db.beacons.update_one(
        {"_id": doc["_id"]},
        {"$set": {"pairing": {"code_hash": hash_secret(code), "expires_at": expires_at}}},
    )
    return PairingCodeOut(code=format_code(code), expires_at=expires_at)


@router.post("/{beacon_id}/unpair", response_model=BeaconOut, dependencies=[Depends(require_admin)])
async def unpair(
    beacon_id: str, db: Database = Depends(get_db), settings: Settings = Depends(get_settings)
):
    """Forget the computer: its token stops working and any outstanding code is void."""
    doc = await _desk_beacon(db, beacon_id)
    await db.beacons.update_one(
        {"_id": doc["_id"]}, {"$set": {"device_token_hash": None, "device": None, "pairing": None}}
    )
    return beacon_out(await db.beacons.find_one({"_id": doc["_id"]}), settings)
