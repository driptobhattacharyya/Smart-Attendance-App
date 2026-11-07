from fastapi import APIRouter, Depends, HTTPException, status

from ..db import Database, get_db
from ..schemas import Office, OfficeIn, OfficeUpdate
from ..security import require_admin
from ..services import office_out, parse_object_id

router = APIRouter(prefix="/offices", tags=["offices"])


@router.get("", response_model=list[Office])
async def list_offices(db: Database = Depends(get_db)):
    """Public: the registration screen needs it before the user has an account."""
    docs = await db.offices.find().sort("name", 1).to_list(length=None)
    return [office_out(d) for d in docs]


@router.post(
    "",
    response_model=Office,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_office(data: OfficeIn, db: Database = Depends(get_db)):
    doc = data.model_dump()
    result = await db.offices.insert_one(doc)
    doc["_id"] = result.inserted_id
    return office_out(doc)


@router.patch("/{office_id}", response_model=Office, dependencies=[Depends(require_admin)])
async def update_office(office_id: str, data: OfficeUpdate, db: Database = Depends(get_db)):
    oid = parse_object_id(office_id, "office")
    changes = data.model_dump(exclude_unset=True)
    if changes:
        await db.offices.update_one({"_id": oid}, {"$set": changes})
    doc = await db.offices.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown office")
    return office_out(doc)
