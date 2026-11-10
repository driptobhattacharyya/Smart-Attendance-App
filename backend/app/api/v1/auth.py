from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from ...auth import consent as consent_service
from ...auth import preauth, service
from ...auth.deps import Ctx, CurrentCtx, client_ip
from ...auth.schemas import (
    ChangePasswordIn,
    ConsentAcceptIn,
    ConsentTextOut,
    ForgotIn,
    InviteAcceptIn,
    LoginIn,
    LoginOut,
    MessageOut,
    RefreshIn,
    ResetIn,
    SessionOut,
    TokenPair,
)
from ...core.config import Settings
from ...core.db import Database, get_database, get_settings_dep
from ...core.email import Outbox
from ...core.security import PasswordHasher

router = APIRouter(tags=["auth"])

Db = Annotated[Database, Depends(get_database)]
Cfg = Annotated[Settings, Depends(get_settings_dep)]


def _hasher(request: Request) -> PasswordHasher:
    return request.app.state.hasher


Hasher = Annotated[PasswordHasher, Depends(_hasher)]


def _ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.post("/auth/login", response_model=LoginOut)
async def login(data: LoginIn, request: Request, db: Db, settings: Cfg, hasher: Hasher):
    return await preauth.login(
        db, settings, hasher, data, ip=client_ip(request), user_agent=_ua(request)
    )


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh(data: RefreshIn, request: Request, db: Db, settings: Cfg):
    return await preauth.refresh(
        db, settings, data.refresh_token, ip=client_ip(request), user_agent=_ua(request)
    )


@router.post("/auth/logout", status_code=204)
async def logout(data: RefreshIn, request: Request, db: Db):
    await preauth.logout(db, data.refresh_token, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/auth/logout-all", status_code=204)
async def logout_all(ctx: CurrentCtx):
    await service.logout_all(ctx)
    return Response(status_code=204)


@router.get("/auth/sessions", response_model=list[SessionOut])
async def sessions(ctx: CurrentCtx):
    return await service.list_sessions(ctx)


@router.delete("/auth/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: uuid.UUID, ctx: CurrentCtx):
    await service.revoke_session(ctx, session_id)
    return Response(status_code=204)


@router.post("/auth/password/forgot", response_model=MessageOut, status_code=202)
async def forgot_password(data: ForgotIn, request: Request, db: Db, settings: Cfg):
    outbox = Outbox()
    await preauth.forgot_password(db, settings, data.email, ip=client_ip(request), outbox=outbox)
    await outbox.flush(request.app.state.email)
    return MessageOut(message="If the account exists, an e-mail with a reset link is on its way.")


@router.post("/auth/password/reset", response_model=MessageOut)
async def reset_password(data: ResetIn, request: Request, db: Db, hasher: Hasher):
    await preauth.reset_password(db, hasher, data.token, data.new_password, ip=client_ip(request))
    return MessageOut(message="Password updated. Sign in with the new password.")


@router.post("/auth/password/change", response_model=MessageOut)
async def change_password(data: ChangePasswordIn, ctx: CurrentCtx):
    await service.change_password(ctx, data.current_password, data.new_password)
    return MessageOut(message="Password changed. Other devices were signed out.")


@router.post("/auth/invite/accept", response_model=LoginOut)
async def accept_invite(
    data: InviteAcceptIn, request: Request, db: Db, settings: Cfg, hasher: Hasher
):
    return await preauth.accept_invite(
        db, settings, hasher, data, ip=client_ip(request), user_agent=_ua(request)
    )


@router.get("/consent/text", response_model=ConsentTextOut)
async def consent_text(ctx: CurrentCtx):
    return await consent_service.consent_text(ctx)


@router.post("/auth/consent", response_model=MessageOut)
async def accept_consent(data: ConsentAcceptIn, ctx: CurrentCtx):
    await consent_service.accept_consent(ctx, data.version)
    return MessageOut(message="Consent recorded")


@router.post("/auth/consent/withdraw", response_model=MessageOut)
async def withdraw_consent(ctx: CurrentCtx):
    await consent_service.withdraw_consent(ctx)
    return MessageOut(message="Consent withdrawn")


__all__ = ["router", "Ctx"]
