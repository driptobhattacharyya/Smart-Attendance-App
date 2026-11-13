from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ...auth.deps import Ctx, client_ip
from ...core.config import Settings
from ...core.db import Database, get_database, get_settings_dep
from ...people.schemas import UserOut
from ...rbac.deps import require_permission
from ...rbac.permissions import ORG_MANAGE
from ...tenancy import service
from ...tenancy.schemas import OrgOut, OrgUpdate, SignupIn, SignupOut
from ...tenancy.signup import signup_org

router = APIRouter(tags=["tenancy"])


@router.post("/orgs/signup", response_model=SignupOut, status_code=201)
async def signup(
    data: SignupIn,
    request: Request,
    db: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
):
    org, owner, tokens = await signup_org(
        db,
        settings,
        request.app.state.hasher,
        data,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return SignupOut(
        **tokens.model_dump(),
        org=OrgOut.model_validate(org),
        user=UserOut.model_validate(owner),
    )


@router.get("/org", response_model=OrgOut)
async def get_org(ctx: Annotated[Ctx, Depends(require_permission(ORG_MANAGE))]):
    return await service.get_org(ctx.db)


@router.patch("/org", response_model=OrgOut)
async def patch_org(data: OrgUpdate, ctx: Annotated[Ctx, Depends(require_permission(ORG_MANAGE))]):
    return await service.update_org(
        ctx,
        name=data.name,
        modules=data.modules,
        settings=data.settings,
        bump_consent_version=data.bump_consent_version,
    )
