"""Endpoints used by the Attendify Desk app itself (no user login)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from ...auth.deps import client_ip
from ...core.config import Settings
from ...core.db import Database, get_database, get_settings_dep
from ...core.errors import AppError
from ...places import desk_system
from ...places.schemas import DeskHeartbeatIn, DeskHeartbeatOut, DeskPairIn, DeskPairOut

router = APIRouter(prefix="/desk", tags=["desk"])

Db = Annotated[Database, Depends(get_database)]
Cfg = Annotated[Settings, Depends(get_settings_dep)]


@router.post("/pair", response_model=DeskPairOut)
async def pair(data: DeskPairIn, request: Request, db: Db, settings: Cfg):
    """Public. The only response that ever contains the device ``secret``."""
    return await desk_system.pair_device(db, settings, data, ip=client_ip(request))


@router.post("/heartbeat", response_model=DeskHeartbeatOut)
async def heartbeat(
    data: DeskHeartbeatIn,
    db: Db,
    settings: Cfg,
    authorization: Annotated[str | None, Header()] = None,
):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "device" or not token.strip():
        raise AppError(
            401, "device_not_paired", "Device not paired", headers={"WWW-Authenticate": "Device"}
        )
    return await desk_system.device_heartbeat(db, settings, token.strip(), data)
