from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from ...auth.deps import Ctx, CurrentCtx
from ...auth.schemas import MessageOut, SessionOut
from ...auth.service import list_sessions
from ...core.pagination import Page
from ...people import service
from ...people.schemas import (
    DepartmentIn,
    DepartmentOut,
    MeOut,
    MeUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from ...rbac.deps import require_any_permission, require_permission
from ...rbac.permissions import PEOPLE_MANAGE, PEOPLE_VIEW_TEAM

router = APIRouter(tags=["people"])

Manager = Annotated[Ctx, Depends(require_permission(PEOPLE_MANAGE))]
Viewer = Annotated[Ctx, Depends(require_any_permission(PEOPLE_MANAGE, PEOPLE_VIEW_TEAM))]


@router.get("/people/me", response_model=MeOut)
async def get_me(ctx: CurrentCtx):
    return await service.me(ctx)


@router.patch("/people/me", response_model=MeOut)
async def patch_me(data: MeUpdate, ctx: CurrentCtx):
    await service.update_me(ctx, data.name, data.phone, set(data.model_fields_set))
    return await service.me(ctx)


@router.get("/people", response_model=Page[UserOut])
async def list_people(
    ctx: Viewer,
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, pattern="^(active|invited|disabled)$"),
    department_id: uuid.UUID | None = None,
    office_id: uuid.UUID | None = None,
    manager_id: uuid.UUID | None = None,
    role_key: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = None,
):
    users, next_cursor = await service.list_people(
        ctx,
        q=q,
        status=status,
        department_id=department_id,
        office_id=office_id,
        manager_id=manager_id,
        role_key=role_key,
        limit=limit,
        cursor=cursor,
    )
    return Page[UserOut](items=[UserOut.model_validate(u) for u in users], next_cursor=next_cursor)


@router.post("/people", response_model=UserOut, status_code=201)
async def create_person(data: UserCreate, ctx: Manager):
    return await service.create_user(ctx, data)


@router.get("/people/{user_id}", response_model=UserOut)
async def get_person(user_id: uuid.UUID, ctx: CurrentCtx):
    return await service.get_user(ctx, user_id)


@router.patch("/people/{user_id}", response_model=UserOut)
async def patch_person(user_id: uuid.UUID, data: UserUpdate, ctx: Manager):
    return await service.update_user(ctx, user_id, data)


@router.post("/people/{user_id}/disable", response_model=UserOut)
async def disable_person(user_id: uuid.UUID, ctx: Manager):
    return await service.disable_user(ctx, user_id)


@router.post("/people/{user_id}/enable", response_model=UserOut)
async def enable_person(user_id: uuid.UUID, ctx: Manager):
    return await service.enable_user(ctx, user_id)


@router.post("/people/{user_id}/invite", response_model=MessageOut)
async def resend_invite(user_id: uuid.UUID, ctx: Manager):
    await service.resend_invite(ctx, user_id)
    return MessageOut(message="Invite sent")


@router.get("/people/{user_id}/sessions", response_model=list[SessionOut])
async def person_sessions(user_id: uuid.UUID, ctx: Manager):
    user = await service.get_user(ctx, user_id)
    return await list_sessions(ctx, user.id)


@router.post("/people/{user_id}/revoke-sessions", response_model=MessageOut)
async def revoke_person_sessions(user_id: uuid.UUID, ctx: Manager):
    count = await service.revoke_sessions_of(ctx, user_id)
    return MessageOut(message=f"{count} session(s) revoked")


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(ctx: CurrentCtx):
    return await service.list_departments(ctx)


@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(data: DepartmentIn, ctx: Manager):
    return await service.create_department(ctx, data.name)


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
async def update_department(department_id: uuid.UUID, data: DepartmentIn, ctx: Manager):
    return await service.update_department(ctx, department_id, data.name)


@router.delete("/departments/{department_id}", status_code=204)
async def delete_department(department_id: uuid.UUID, ctx: Manager):
    await service.delete_department(ctx, department_id)
    return Response(status_code=204)
