from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError

from ..config import Settings, get_settings
from ..db import Database, get_db, next_employee_id, utcnow
from ..schemas import AuthOut, LoginIn, RegisterIn, UserOut
from ..security import Role, create_access_token, get_current_user, hash_password, verify_password
from ..services import get_office, office_out, user_out

router = APIRouter(prefix="/auth", tags=["auth"])


async def _auth_response(db: Database, user: dict, settings: Settings) -> AuthOut:
    office = await get_office(db, user.get("office_id"))
    out = user_out(user)
    return AuthOut(
        access_token=create_access_token(user["employee_id"], user["role"], settings),
        user=out,
        office=office_out(office) if office else None,
        employee_id=out.employee_id,
        bluetooth_device_id=out.bluetooth_device_id,
        face_enrolled=out.face_enrolled,
    )


async def create_user(
    db: Database, data: RegisterIn, role: Role = Role.employee, team_id: str | None = None
) -> dict:
    if not await get_office(db, data.office_id):
        raise HTTPException(422, "Unknown office")
    email = data.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    user = {
        "employee_id": await next_employee_id(db),
        "name": data.name.strip(),
        "email": email,
        "phone": data.phone,
        "password_hash": hash_password(data.password),
        "role": role.value,
        # Until an admin assigns teams, everyone at an office forms one team.
        "team_id": team_id or data.office_id,
        "office_id": data.office_id,
        "bluetooth_device_id": data.bluetooth_device,
        "active": True,
        "created_at": utcnow(),
    }
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists") from exc
    return user


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterIn,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Employee self-registration from the mobile app. Returns a token so the app can
    immediately enrol the user's face."""
    if not settings.allow_self_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Self-registration is disabled")
    user = await create_user(db, data)
    return await _auth_response(db, user, settings)


@router.post("/login", response_model=AuthOut)
async def login(
    data: LoginIn,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    identifier = data.employee_id.strip()
    query = {"email": identifier.lower()} if "@" in identifier else {"employee_id": identifier}
    user = await db.users.find_one(query)
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid employee ID or password")
    if not user.get("active", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been disabled")
    return await _auth_response(db, user, settings)


@router.get("/me", response_model=UserOut)
async def me(user: dict = Depends(get_current_user)):
    return user_out(user)
