from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ..tenancy.models import Role
from .permissions import BUILTIN_ROLES


async def seed_builtin_roles(session: AsyncSession, org_id: uuid.UUID) -> None:
    for role in BUILTIN_ROLES:
        session.add(
            Role(
                org_id=org_id,
                key=role.key,
                name=role.name,
                permissions=list(role.permissions),
                builtin=True,
            )
        )
    await session.flush()
