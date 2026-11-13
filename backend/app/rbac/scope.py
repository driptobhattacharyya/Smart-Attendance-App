"""Team scope: everyone below a user in the reporting tree (``users.manager_id``)."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.orm import aliased

from ..people.models import User


def team_user_ids(root_user_id: uuid.UUID, *, include_self: bool = False) -> Select:
    """A subquery of user ids reporting (directly or indirectly) to ``root_user_id``.

    ``UNION`` (not ``UNION ALL``) makes the recursion terminate even if bad data had a cycle.
    Row level security applies inside the CTE, so it never leaves the caller's organization.
    Use as ``User.id.in_(team_user_ids(me))``. Nothing is cached: it is evaluated per query.
    """
    base = select(User.id.label("id")).where(User.manager_id == root_user_id)
    tree = base.cte("team", recursive=True)
    report = aliased(User)
    tree = tree.union(select(report.id).where(report.manager_id == tree.c.id))
    query = select(tree.c.id)
    if include_self:
        query = query.union(select(User.id).where(User.id == root_user_id))
    return query


async def is_in_team(session, root_user_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    found = await session.execute(
        select(User.id).where(User.id == user_id, User.id.in_(team_user_ids(root_user_id)))
    )
    return found.first() is not None
