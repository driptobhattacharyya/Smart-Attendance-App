from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from ..config import Settings, get_settings
from ..db import Database, get_db, utcnow
from ..face import FaceError, FaceProvider, FaceUnavailableError
from ..schemas import FaceEnrollOut, FaceVerifyOut
from ..security import get_consenting_user

router = APIRouter(prefix="/face", tags=["face"])


def get_face_provider(request: Request) -> FaceProvider:
    provider = getattr(request.app.state, "face_provider", None)
    if provider is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Face recognition is not available on this server (not configured)",
        )
    return provider


async def _read_image(image: UploadFile, settings: Settings) -> bytes:
    data = await image.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(422, "Empty image")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image is too large")
    return data


@router.post("/enroll", response_model=FaceEnrollOut)
async def enroll(
    image: UploadFile = File(...),
    user: dict = Depends(get_consenting_user),
    db: Database = Depends(get_db),
    provider: FaceProvider = Depends(get_face_provider),
    settings: Settings = Depends(get_settings),
):
    """Register the user's face. Re-enrolment needs a manager to reset it first,
    so a stolen password alone cannot be used to swap in someone else's face."""
    if provider.is_enrolled(user):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Face already registered. Ask your manager to reset it."
        )
    data = await _read_image(image, settings)
    try:
        fields = await provider.enroll(user, data)
    except FaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FaceUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    await db.users.update_one(
        {"employee_id": user["employee_id"]},
        {"$set": {**fields, "face_enrolled_at": utcnow()}},
    )
    return FaceEnrollOut(enrolled=True, face_id=user["employee_id"])


@router.post("/verify", response_model=FaceVerifyOut)
async def verify(
    image: UploadFile = File(...),
    user: dict = Depends(get_consenting_user),
    db: Database = Depends(get_db),
    provider: FaceProvider = Depends(get_face_provider),
    settings: Settings = Depends(get_settings),
):
    """Compare a live photo with the enrolled face. A match unlocks check-in for
    ``FACE_VERIFICATION_TTL_MINUTES``."""
    if not provider.is_enrolled(user):
        raise HTTPException(status.HTTP_409_CONFLICT, "No face registered for this account")
    data = await _read_image(image, settings)
    try:
        verified, score = await provider.verify(user, data)
    except FaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FaceUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if not verified:
        return FaceVerifyOut(verified=False, score=round(score, 4), message="Face not recognized")

    now = utcnow()
    await db.users.update_one(
        {"employee_id": user["employee_id"]}, {"$set": {"face_verified_at": now}}
    )
    return FaceVerifyOut(
        verified=True,
        score=round(score, 4),
        valid_until=now + timedelta(minutes=settings.face_verification_ttl_minutes),
        message="Face recognized",
    )
