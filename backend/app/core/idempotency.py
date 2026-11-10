"""Generic ``Idempotency-Key`` storage (used by check-in/out and heartbeats in step 2).

``run_idempotent`` runs ``handler`` at most once per (org, key): the key row is inserted in the
caller's tenant transaction, so a concurrent retry blocks on the unique index until the first
request commits and then replays its stored response.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Integer, Text, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .errors import AppError, conflict
from .models import Base
from .security import utcnow


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def run_idempotent(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    key: str,
    scope: str,
    payload: Any,
    handler: Callable[[], Awaitable[tuple[int, Any]]],
) -> tuple[int, Any, bool]:
    """Returns ``(status, body, replayed)``. ``handler`` returns ``(status, json_body)``.

    Same key + different scope/payload is a 422 (a client bug); the same key whose first request
    is still running in another transaction is waited for by the database, never run twice.
    """
    request_hash = fingerprint({"scope": scope, "payload": payload})
    inserted = (
        await session.execute(
            insert(IdempotencyKey)
            .values(
                org_id=org_id,
                key=key,
                user_id=user_id,
                scope=scope,
                request_hash=request_hash,
            )
            .on_conflict_do_nothing()
            .returning(IdempotencyKey.key)
        )
    ).first()
    if inserted is not None:
        status, body = await handler()
        await session.execute(
            IdempotencyKey.__table__.update()
            .where(IdempotencyKey.org_id == org_id, IdempotencyKey.key == key)
            .values(response_status=status, response_body=body)
        )
        return status, body, False

    row = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.org_id == org_id, IdempotencyKey.key == key)
        )
    ).scalar_one()
    if row.request_hash != request_hash:
        raise AppError(
            422, "idempotency_key_reuse", "Idempotency-Key was already used for another request"
        )
    if row.response_status is None:
        raise conflict("idempotency_in_progress", "A request with this key is still in progress")
    return row.response_status, row.response_body, True


async def purge_expired(session: AsyncSession, older_than: timedelta = timedelta(days=2)) -> int:
    result = await session.execute(
        delete(IdempotencyKey).where(IdempotencyKey.created_at < utcnow() - older_than)
    )
    return result.rowcount or 0
