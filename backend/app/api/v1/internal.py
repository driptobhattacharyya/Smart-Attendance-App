"""Scheduled-job endpoints, called by EventBridge with a shared secret (not a user token)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel

from ...core.config import Settings
from ...core.db import Database, get_database, get_settings_dep
from ...core.errors import AppError, bad_request, unauthorized
from ...core.security import constant_time_equal
from ...jobs import service

SECRET_HEADER = "X-Internal-Job-Secret"


async def require_job_secret(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    secret: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
) -> None:
    if not settings.internal_job_secret:
        raise AppError(503, "jobs_disabled", "INTERNAL_JOB_SECRET is not configured")
    if not secret or not constant_time_equal(secret, settings.internal_job_secret):
        raise unauthorized("Invalid job secret", "invalid_job_secret")


router = APIRouter(
    prefix="/internal/jobs",
    tags=["internal"],
    dependencies=[Depends(require_job_secret)],
    include_in_schema=True,
)

Db = Annotated[Database, Depends(get_database)]


class RecomputeIn(BaseModel):
    start: date
    end: date
    org_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


@router.post("/close-stale-sessions")
async def close_stale_sessions(db: Db):
    return await service.close_stale_sessions(db)


@router.post("/retention")
async def retention(db: Db, request: Request):
    return await service.retention(db, request.app.state.settings)


@router.post("/recompute")
async def recompute(data: RecomputeIn, db: Db):
    try:
        return await service.recompute(
            db, start=data.start, end=data.end, org_id=data.org_id, user_id=data.user_id
        )
    except ValueError as exc:
        raise bad_request("invalid_range", str(exc)) from exc
