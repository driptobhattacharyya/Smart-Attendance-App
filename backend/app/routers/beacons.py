from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..db import Database, get_db
from ..schemas import BeaconIn, BeaconOut, BeaconUpdate
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
    return [beacon_out(d) for d in docs]


@router.get("/mine", response_model=list[BeaconOut])
async def my_beacons(user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    """The beacons this phone should scan for."""
    return [beacon_out(d) for d in await beacons_for_user(db, user)]


@router.post(
    "",
    response_model=BeaconOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_beacon(data: BeaconIn, db: Database = Depends(get_db)):
    doc = data.model_dump()
    await _check_refs(db, doc)
    result = await db.beacons.insert_one(doc)
    doc["_id"] = result.inserted_id
    return beacon_out(doc)


@router.patch("/{beacon_id}", response_model=BeaconOut, dependencies=[Depends(require_admin)])
async def update_beacon(beacon_id: str, data: BeaconUpdate, db: Database = Depends(get_db)):
    oid = parse_object_id(beacon_id, "beacon")
    changes = data.model_dump(exclude_unset=True)
    await _check_refs(db, changes)
    if changes:
        await db.beacons.update_one({"_id": oid}, {"$set": changes})
    doc = await db.beacons.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown beacon")
    return beacon_out(doc)


@router.delete(
    "/{beacon_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)]
)
async def delete_beacon(beacon_id: str, db: Database = Depends(get_db)):
    result = await db.beacons.delete_one({"_id": parse_object_id(beacon_id, "beacon")})
    if result.deleted_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown beacon")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
