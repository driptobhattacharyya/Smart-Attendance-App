from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..db import Database, get_db, utcnow
from ..face import FaceEngine, FaceError
from ..schemas import FaceEnrollOut, FaceVerifyOut
from ..security import get_current_user

router = APIRouter(prefix="/face", tags=["face"])


def get_face_engine(request: Request) -> FaceEngine:
    engine = getattr(request.app.state, "face_engine", None)
    if engine is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Face recognition is not available on this server (models not loaded)",
        )
    return engine


async def _embedding_from_upload(
    image: UploadFile, engine: FaceEngine, settings: Settings
) -> list[float]:
    data = await image.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(422, "Empty image")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image is too large")
    try:
        return await run_in_threadpool(engine.embed, data)
    except FaceError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/enroll", response_model=FaceEnrollOut)
async def enroll(
    image: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    engine: FaceEngine = Depends(get_face_engine),
    settings: Settings = Depends(get_settings),
):
    """Store the user's face embedding. Re-enrolment needs a manager to reset it first,
    so a stolen password alone cannot be used to swap in someone else's face."""
    if user.get("face_embedding"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Face already registered. Ask your manager to reset it."
        )
    embedding = await _embedding_from_upload(image, engine, settings)
    await db.users.update_one(
        {"employee_id": user["employee_id"]},
        {"$set": {"face_embedding": embedding, "face_enrolled_at": utcnow()}},
    )
    return FaceEnrollOut(enrolled=True, face_id=user["employee_id"])


@router.post("/verify", response_model=FaceVerifyOut)
async def verify(
    image: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
    engine: FaceEngine = Depends(get_face_engine),
    settings: Settings = Depends(get_settings),
):
    """Compare a live photo with the enrolled face. A match unlocks check-in for
    ``FACE_VERIFICATION_TTL_MINUTES``."""
    stored = user.get("face_embedding")
    if not stored:
        raise HTTPException(status.HTTP_409_CONFLICT, "No face registered for this account")
    embedding = await _embedding_from_upload(image, engine, settings)
    score = engine.similarity(stored, embedding)
    if score < settings.face_match_threshold:
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
