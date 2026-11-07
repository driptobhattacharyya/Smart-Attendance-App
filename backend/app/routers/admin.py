from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings, get_settings
from ..db import Database, get_db, utcnow
from ..retention import prune_presence
from ..schemas import AdminCreateUser, UserOut, UserUpdate
from ..security import require_admin
from ..services import get_office, user_out
from .auth import create_user

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users", response_model=list[UserOut])
async def list_users(db: Database = Depends(get_db), settings: Settings = Depends(get_settings)):
    docs = await db.users.find().sort("employee_id", 1).to_list(length=None)
    return [user_out(d, settings) for d in docs]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def add_user(
    data: AdminCreateUser,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = await create_user(db, data, role=data.role, team_id=data.team_id)
    return user_out(user, settings)


@router.patch("/users/{employee_id}", response_model=UserOut)
async def update_user(
    employee_id: str,
    data: UserUpdate,
    admin: dict = Depends(require_admin),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    changes = data.model_dump(exclude_unset=True, mode="json")
    if "office_id" in changes and not await get_office(db, changes["office_id"]):
        raise HTTPException(422, "Unknown office")
    if "office_id" in changes and "team_id" not in changes:
        # Teams follow offices unless an admin sets a team explicitly.
        changes["team_id"] = changes["office_id"]
    if employee_id == admin["employee_id"] and (
        changes.get("active") is False or changes.get("role", "admin") != "admin"
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot demote or disable yourself")
    if changes:
        await db.users.update_one({"employee_id": employee_id}, {"$set": changes})
    doc = await db.users.find_one({"employee_id": employee_id})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user_out(doc, settings)


@router.post("/maintenance/prune")
async def prune(db: Database = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Remove presence logs and check-in/out coordinates older than PRESENCE_RETENTION_DAYS.
    Worked/away totals are kept."""
    return {"sessions_pruned": await prune_presence(db, settings, utcnow())}
