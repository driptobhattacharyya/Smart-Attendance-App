from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from ...auth.deps import Ctx, CurrentCtx
from ...places import service
from ...places.schemas import (
    DeskDeviceCreate,
    DeskDeviceOut,
    DeskDeviceUpdate,
    OfficeCreate,
    OfficeOut,
    OfficeUpdate,
    PairingCodeOut,
)
from ...rbac.deps import require_permission
from ...rbac.permissions import PLACES_MANAGE

router = APIRouter(prefix="/places", tags=["places"])

Places = Annotated[Ctx, Depends(require_permission(PLACES_MANAGE))]


@router.get("/offices", response_model=list[OfficeOut])
async def list_offices(ctx: CurrentCtx):
    """Every signed-in user: phones need the geofences."""
    return await service.list_offices(ctx)


@router.post("/offices", response_model=OfficeOut, status_code=201)
async def create_office(data: OfficeCreate, ctx: Places):
    return await service.create_office(ctx, data)


@router.get("/offices/{office_id}", response_model=OfficeOut)
async def get_office(office_id: uuid.UUID, ctx: CurrentCtx):
    return await service.get_office(ctx, office_id)


@router.patch("/offices/{office_id}", response_model=OfficeOut)
async def patch_office(office_id: uuid.UUID, data: OfficeUpdate, ctx: Places):
    return await service.update_office(ctx, office_id, data)


@router.delete("/offices/{office_id}", status_code=204)
async def delete_office(office_id: uuid.UUID, ctx: Places):
    await service.delete_office(ctx, office_id)
    return Response(status_code=204)


@router.get("/desk-devices/mine", response_model=list[DeskDeviceOut])
async def my_devices(ctx: CurrentCtx):
    return await service.devices_for_me(ctx)


@router.get("/desk-devices", response_model=list[DeskDeviceOut])
async def list_devices(ctx: Places, kind: Literal["tag", "desk_app"] | None = Query(default=None)):
    desk_uuid = ctx.org.settings["desk_uuid"]
    return [service.device_out(d, desk_uuid) for d in await service.list_devices(ctx, kind)]


@router.post("/desk-devices", response_model=DeskDeviceOut, status_code=201)
async def create_device(data: DeskDeviceCreate, ctx: Places):
    device = await service.create_device(ctx, data)
    return service.device_out(device, ctx.org.settings["desk_uuid"])


@router.get("/desk-devices/{device_id}", response_model=DeskDeviceOut)
async def get_device(device_id: uuid.UUID, ctx: Places):
    return service.device_out(
        await service.get_device(ctx, device_id), ctx.org.settings["desk_uuid"]
    )


@router.patch("/desk-devices/{device_id}", response_model=DeskDeviceOut)
async def patch_device(device_id: uuid.UUID, data: DeskDeviceUpdate, ctx: Places):
    device = await service.update_device(ctx, device_id, data)
    return service.device_out(device, ctx.org.settings["desk_uuid"])


@router.delete("/desk-devices/{device_id}", status_code=204)
async def delete_device(device_id: uuid.UUID, ctx: Places):
    await service.delete_device(ctx, device_id)
    return Response(status_code=204)


@router.post("/desk-devices/{device_id}/pairing-code", response_model=PairingCodeOut)
async def pairing_code(device_id: uuid.UUID, ctx: Places):
    code, expires = await service.create_pairing_code(ctx, device_id)
    return PairingCodeOut(code=code, expires_at=expires)


@router.post("/desk-devices/{device_id}/unpair", response_model=DeskDeviceOut)
async def unpair(device_id: uuid.UUID, ctx: Places):
    device = await service.unpair_device(ctx, device_id)
    return service.device_out(device, ctx.org.settings["desk_uuid"])
