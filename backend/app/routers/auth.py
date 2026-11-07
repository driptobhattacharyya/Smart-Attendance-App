from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pymongo.errors import DuplicateKeyError

from ..config import Settings, get_settings
from ..db import Database, get_db, next_employee_id, utcnow
from ..presence import SessionClosedError, apply_presence
from ..schemas import AuthOut, ConsentIn, LoginIn, RegisterIn, UserOut
from ..security import Role, create_access_token, get_current_user, hash_password, verify_password
from ..services import get_office, office_out, user_out

router = APIRouter(prefix="/auth", tags=["auth"])


async def _auth_response(db: Database, user: dict, settings: Settings) -> AuthOut:
    office = await get_office(db, user.get("office_id"))
    out = user_out(user, settings)
    return AuthOut(
        access_token=create_access_token(user["employee_id"], user["role"], settings),
        user=out,
        office=office_out(office) if office else None,
        employee_id=out.employee_id,
        bluetooth_device_id=out.bluetooth_device_id,
        face_enrolled=out.face_enrolled,
        consent_required=out.consent_required,
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
async def me(user: dict = Depends(get_current_user), settings: Settings = Depends(get_settings)):
    return user_out(user, settings)


@router.post("/consent", response_model=UserOut)
async def accept_consent(
    data: ConsentIn,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Record that the user has read and accepted the current consent text."""
    if data.version != settings.consent_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Consent text has changed; the current version is {settings.consent_version}",
        )
    consent = {"version": data.version, "accepted_at": utcnow()}
    await db.users.update_one({"employee_id": user["employee_id"]}, {"$set": {"consent": consent}})
    return user_out({**user, "consent": consent}, settings)


@router.post("/consent/withdraw", response_model=UserOut)
async def withdraw_consent(
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Withdraw consent: stops all tracking. An open session is checked out first."""
    now = utcnow()
    open_doc = await db.attendance_sessions.find_one(
        {"employee_id": user["employee_id"], "ended_at": None, "source": "app"}
    )
    if open_doc:
        try:
            await apply_presence(
                db,
                open_doc["_id"],
                now,
                settings.heartbeat_grace_minutes * 60,
                state=None,
                end=True,
                extra_set={"check_out": {"reason": "consent withdrawn"}},
            )
        except SessionClosedError:
            pass  # closed by someone else in the meantime; nothing left to do
    await db.users.update_one(
        {"employee_id": user["employee_id"]},
        {"$unset": {"consent": "", "face_verified_at": ""}},
    )
    return user_out({k: v for k, v in user.items() if k != "consent"}, settings)


# Fields of the user document that are never part of an export.
_PROFILE_HIDDEN = {"_id", "password_hash", "face_embedding", "azure_person_id", "face_verified_at"}


@router.get("/me/export")
async def export_my_data(
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Right of access: everything stored about the caller, as JSON. Deliberately does not
    require consent, so people can still get their data after withdrawing it."""
    employee_id = user["employee_id"]
    sessions = (
        await db.attendance_sessions.find({"employee_id": employee_id})
        .sort("started_at", 1)
        .to_list(length=None)
    )
    requests = (
        await db.attendance_requests.find({"employee_id": employee_id})
        .sort("submitted_at", 1)
        .to_list(length=None)
    )
    profile = {k: v for k, v in user.items() if k not in _PROFILE_HIDDEN}
    profile["face_enrolled"] = user_out(user, settings).face_enrolled

    def encode(doc: dict) -> dict:
        doc = {("id" if k == "_id" else k): v for k, v in doc.items()}
        return jsonable_encoder(doc, custom_encoder={ObjectId: str})

    return {
        "generated_at": jsonable_encoder(utcnow()),
        "profile": encode(profile),
        "sessions": [encode(d) for d in sessions],
        "requests": [encode(d) for d in requests],
    }
