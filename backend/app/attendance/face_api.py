"""Face challenge / enrolment / verification on top of ``app.face`` (docs/face-liveness.md).

Frames are read into memory, handed to the provider in a worker thread and never stored or logged.
Challenges are signed, stateless and single use: the id is recorded in ``face_challenges_used``
*before* the frames are evaluated, pass or fail.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from ..audit.models import AuditLog
from ..auth.deps import Ctx
from ..core.errors import AppError, conflict, error_body, not_found
from ..core.security import utcnow
from ..face import (
    ChallengeError,
    FaceConfig,
    FaceError,
    FaceUnavailableError,
    build_opencv_provider,
    new_challenge,
    sign_challenge,
    verify_challenge_token,
)
from ..people.models import User
from ..places.models import Office
from ..rbac.permissions import PEOPLE_MANAGE, PEOPLE_VIEW_TEAM
from ..rbac.scope import is_in_team
from .face_store import (
    decrypt_enrollment,
    delete_enrollment,
    encrypt_enrollment,
    get_enrollment,
)
from .models import FaceChallengeUsed, FaceEnrollment
from .store import append_event, get_user, local_date, lock_user, office_info

logger = logging.getLogger("attendify.face")

MAX_FAILED_VERIFICATIONS = 10
FAILED_WINDOW = timedelta(minutes=15)

# OpenCV nets are not safe for concurrent forward passes on one instance.
_PROVIDER_LOCK = threading.Lock()


def challenge_key(settings) -> bytes:
    return hashlib.sha256(b"attendify-face-challenge|" + settings.jwt_secret.encode()).digest()


def face_config(ctx: Ctx) -> FaceConfig:
    cfg = getattr(ctx.state, "face_config", None)
    if cfg is None:
        cfg = FaceConfig.from_env()
        if ctx.state is not None:
            ctx.state.face_config = cfg
    return cfg


async def get_provider(ctx: Ctx):
    provider = getattr(ctx.state, "face_provider", None)
    if provider is not None:
        return provider
    try:
        provider = await asyncio.to_thread(build_opencv_provider, face_config(ctx))
    except Exception as exc:  # models missing, bad install, out of memory...
        logger.exception("Could not build the face provider")
        raise AppError(503, "face_unavailable", "Face verification is not available") from exc
    if ctx.state is not None:
        ctx.state.face_provider = provider
    return provider


async def _run(fn, *args):
    def call():
        with _PROVIDER_LOCK:
            return fn(*args)

    try:
        return await asyncio.to_thread(call)
    except FaceUnavailableError as exc:
        raise AppError(503, "face_unavailable", "Face verification is not available") from exc


async def read_frames(
    files: list[UploadFile], cfg: FaceConfig, *, low: int, high: int
) -> list[bytes]:
    if not low <= len(files) <= high:
        raise AppError(
            422,
            "bad_frame_count",
            f"Send between {low} and {high} frames",
            {"min": low, "max": high},
        )
    frames: list[bytes] = []
    for f in files:
        data = await f.read(cfg.max_frame_bytes + 1)
        if len(data) > cfg.max_frame_bytes:
            raise AppError(413, "frame_too_large", "A frame is too large")
        if not data:
            raise AppError(422, "empty_frame", "A frame is empty")
        frames.append(data)
    return frames


# ------------------------------------------------------------------------------ challenge


async def new_signed_challenge(ctx: Ctx) -> dict[str, Any]:
    cfg = face_config(ctx)
    ch = new_challenge(
        subject=str(ctx.user_id), actions=cfg.challenge_actions, expires_in=cfg.challenge_ttl_s
    )
    return {
        "challenge_id": ch.id,
        "action": ch.action,
        "expires_in": ch.expires_in,
        "token": sign_challenge(ch, challenge_key(ctx.settings)),
    }


# ------------------------------------------------------------------------------ enrol


async def enroll(ctx: Ctx, files: list[UploadFile]) -> dict[str, Any]:
    cfg = face_config(ctx)
    await lock_user(ctx.db, ctx.org_id, ctx.user_id)
    if await get_enrollment(ctx.db, ctx.user_id) is not None:
        raise conflict(
            "face_already_enrolled",
            "A face is already enrolled; ask your administrator to reset it first",
        )
    frames = await read_frames(files, cfg, low=cfg.enroll_min_frames, high=cfg.enroll_max_frames)
    provider = await get_provider(ctx)
    try:
        enrolled = await _run(provider.enroll, frames)
    except FaceError as exc:
        raise AppError(422, "face_rejected", str(exc)) from exc
    row = FaceEnrollment(
        id=uuid.uuid4(),
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        provider=enrolled.provider,
        model=enrolled.model,
        data_enc=encrypt_enrollment(ctx.settings, ctx.user_id, enrolled),
        frames_used=enrolled.frames_used,
    )
    ctx.db.add(row)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise conflict("face_already_enrolled", "A face is already enrolled") from exc
    await ctx.audit(
        "face.enroll",
        "user",
        ctx.user_id,
        after={"provider": row.provider, "model": row.model, "frames_used": row.frames_used},
    )
    return face_status_body(row)


def face_status_body(row: FaceEnrollment | None) -> dict[str, Any]:
    return {
        "enrolled": row is not None,
        "provider": row.provider if row else None,
        "enrolled_at": row.enrolled_at.isoformat() if row else None,
    }


async def status(ctx: Ctx) -> dict[str, Any]:
    return face_status_body(await get_enrollment(ctx.db, ctx.user_id))


# ------------------------------------------------------------------------------ verify


async def _recent_failures(ctx: Ctx, now: datetime) -> int:
    return (
        await ctx.db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.actor_user_id == ctx.user_id,
                AuditLog.action == "face.verify_failed",
                AuditLog.at > now - FAILED_WINDOW,
            )
        )
    ).scalar_one()


async def verify(ctx: Ctx, token: str, files: list[UploadFile]) -> JSONResponse:
    """Returns the HTTP response itself: a failed verification must *commit* (it consumed the
    challenge and is audited), so it is a returned 403, not a raised error."""
    cfg = face_config(ctx)
    now = utcnow()
    try:
        challenge = verify_challenge_token(
            token, challenge_key(ctx.settings), subject=str(ctx.user_id), now=now.timestamp()
        )
    except ChallengeError as exc:
        code = "challenge_expired" if exc.reason == "expired" else "invalid_challenge"
        raise AppError(
            400, code, "The face challenge is not valid", {"reason": exc.reason}
        ) from exc

    await lock_user(ctx.db, ctx.org_id, ctx.user_id)
    row = await get_enrollment(ctx.db, ctx.user_id)
    if row is None:
        raise conflict("face_not_enrolled", "Enrol your face first")
    if await _recent_failures(ctx, now) >= MAX_FAILED_VERIFICATIONS:
        raise AppError(
            429,
            "rate_limited",
            "Too many failed face checks; try again later",
            headers={"Retry-After": str(int(FAILED_WINDOW.total_seconds()))},
        )
    frames = await read_frames(files, cfg, low=1, high=cfg.max_frames)

    expires = datetime.fromtimestamp(challenge.issued_at + challenge.expires_in, UTC)
    consumed = (
        await ctx.db.execute(
            insert(FaceChallengeUsed)
            .values(
                org_id=ctx.org_id,
                challenge_id=challenge.id,
                user_id=ctx.user_id,
                expires_at=expires,
            )
            .on_conflict_do_nothing()
            .returning(FaceChallengeUsed.challenge_id)
        )
    ).first()
    if consumed is None:
        raise conflict("challenge_used", "This face challenge was already used")

    provider = await get_provider(ctx)
    enrolled = decrypt_enrollment(ctx.settings, row)
    result = await _run(provider.verify, enrolled, frames, challenge)
    if not result.verified:
        await ctx.audit("face.verify_failed", "user", ctx.user_id, after={"reason": result.reason})
        return JSONResponse(
            error_body(
                "face_verification_failed",
                "Face verification failed",
                {"reason": result.reason},
            ),
            status_code=403,
        )

    user = await get_user(ctx.db, ctx.user_id)
    office = None
    if user.office_id:
        office = (
            await ctx.db.execute(select(Office).where(Office.id == user.office_id))
        ).scalar_one_or_none()
    tz = office_info(office).timezone
    event = await append_event(
        ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        type="face_verified",
        occurred_at=now,
        source="app",
        day=local_date(now, tz),
        payload={
            "score": round(result.score, 4),
            "liveness_score": round(result.liveness_score, 4),
            "action": challenge.action,
            "challenge_id": challenge.id,
        },
        actor_user_id=ctx.user_id,
    )
    valid_until = now + timedelta(minutes=ctx.settings.face_verification_ttl_minutes)
    return JSONResponse(
        {
            "verified": True,
            "score": round(result.score, 4),
            "liveness_score": round(result.liveness_score, 4),
            "event_id": str(event.id),
            "valid_until": valid_until.isoformat(),
        }
    )


# ------------------------------------------------------------------------------ delete


async def delete_face(ctx: Ctx, user_id: uuid.UUID) -> None:
    """Self, an administrator (``people.manage``), or the person's manager (team scope)."""
    if user_id != ctx.user_id:
        allowed = ctx.has(PEOPLE_MANAGE) or (
            ctx.has(PEOPLE_VIEW_TEAM) and await is_in_team(ctx.db, ctx.user_id, user_id)
        )
        if not allowed:
            raise not_found("Person")
        if (await ctx.db.execute(select(User.id).where(User.id == user_id))).first() is None:
            raise not_found("Person")
    if await delete_enrollment(ctx.db, user_id):
        await ctx.audit("face.delete", "user", user_id)
