from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from ...attendance import face_api
from ...auth.deps import Ctx, CurrentCtx, require_consent
from ...core.errors import forbidden
from ...rbac.permissions import ATTENDANCE_SELF

router = APIRouter(tags=["face"])


async def _face_user(ctx: Annotated[Ctx, Depends(require_consent)]) -> Ctx:
    ctx.require(ATTENDANCE_SELF)
    if not ctx.org.modules.get("face"):
        raise forbidden("The 'face' module is not enabled", "module_disabled", module="face")
    return ctx


FaceUser = Annotated[Ctx, Depends(_face_user)]


@router.get("/face/challenge")
async def challenge(ctx: FaceUser):
    return await face_api.new_signed_challenge(ctx)


@router.get("/face/status")
async def face_status(ctx: FaceUser):
    return await face_api.status(ctx)


@router.post("/face/enroll", status_code=201)
async def enroll(ctx: FaceUser, frames: Annotated[list[UploadFile], File()]):
    return await face_api.enroll(ctx, frames)


@router.post("/face/verify")
async def verify(
    ctx: FaceUser,
    challenge_token: Annotated[str, Form(max_length=2000)],
    frames: Annotated[list[UploadFile], File()],
):
    return await face_api.verify(ctx, challenge_token, frames)


@router.delete("/face/me", status_code=204)
async def delete_my_face(ctx: CurrentCtx):
    await face_api.delete_face(ctx, ctx.user_id)
    return Response(status_code=204)


@router.delete("/people/{user_id}/face", status_code=204)
async def delete_face(user_id: uuid.UUID, ctx: CurrentCtx):
    """People managers and the person's managers reset an enrolment (e.g. after a bad scan)."""
    await face_api.delete_face(ctx, user_id)
    return Response(status_code=204)
