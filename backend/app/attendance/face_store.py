"""Face enrolment storage: embeddings are encrypted at rest with DATA_KEY (AES-GCM, bound to the
user id), and removed when the user is disabled or an administrator resets them."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import crypto
from ..core.config import Settings
from ..face import EnrolledFace
from .models import FaceEnrollment


def _aad(user_id: uuid.UUID) -> bytes:
    return b"face_enrollment:" + user_id.bytes


def encrypt_enrollment(settings: Settings, user_id: uuid.UUID, enrolled: EnrolledFace) -> bytes:
    raw = json.dumps(enrolled.to_dict(), separators=(",", ":")).encode()
    return crypto.encrypt(settings.data_key_bytes, raw, _aad(user_id))


def decrypt_enrollment(settings: Settings, row: FaceEnrollment) -> EnrolledFace:
    raw = crypto.decrypt(settings.data_key_bytes, bytes(row.data_enc), _aad(row.user_id))
    return EnrolledFace.from_dict(json.loads(raw))


async def get_enrollment(db: AsyncSession, user_id: uuid.UUID) -> FaceEnrollment | None:
    return (
        await db.execute(select(FaceEnrollment).where(FaceEnrollment.user_id == user_id))
    ).scalar_one_or_none()


async def delete_enrollment(db: AsyncSession, user_id: uuid.UUID) -> bool:
    result = await db.execute(delete(FaceEnrollment).where(FaceEnrollment.user_id == user_id))
    return bool(result.rowcount)
