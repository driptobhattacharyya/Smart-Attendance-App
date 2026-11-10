"""Route guards: ``Depends(require_permission("people.manage"))`` returns the ``Ctx``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..auth.deps import Ctx, CurrentCtx
from ..core.errors import forbidden
from .permissions import ALL_PERMISSIONS


def require_permission(*permissions: str) -> Callable[..., Awaitable[Ctx]]:
    """Caller must hold *all* of ``permissions``."""
    unknown = set(permissions) - set(ALL_PERMISSIONS)
    if unknown:
        raise ValueError(f"unknown permission(s): {sorted(unknown)}")

    async def dependency(ctx: CurrentCtx) -> Ctx:
        for permission in permissions:
            ctx.require(permission)
        return ctx

    return dependency


def require_any_permission(*permissions: str) -> Callable[..., Awaitable[Ctx]]:
    """Caller must hold at least one of ``permissions``."""
    unknown = set(permissions) - set(ALL_PERMISSIONS)
    if unknown:
        raise ValueError(f"unknown permission(s): {sorted(unknown)}")

    async def dependency(ctx: CurrentCtx) -> Ctx:
        if not any(ctx.has(p) for p in permissions):
            raise forbidden("You do not have permission to do this", "forbidden")
        return ctx

    return dependency


def require_module(name: str) -> Callable[..., Awaitable[Ctx]]:
    """The organization must have the module (entitlement) switched on."""

    async def dependency(ctx: CurrentCtx) -> Ctx:
        if not ctx.org.modules.get(name):
            raise forbidden(f"The '{name}' module is not enabled", "module_disabled", module=name)
        return ctx

    return dependency
