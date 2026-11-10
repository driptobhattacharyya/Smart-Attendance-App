"""Cursor (keyset) pagination: ``?limit=&cursor=`` and ``{"items": [...], "next_cursor": ...}``."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import bad_request

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"dt": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"u": str(value)}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "dt" in value:
            return datetime.fromisoformat(value["dt"])
        if "u" in value:
            return uuid.UUID(value["u"])
        raise ValueError("bad cursor value")
    return value


def encode_cursor(values: Sequence[Any]) -> str:
    raw = json.dumps([_encode_value(v) for v in values], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, expected: int) -> list[Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = [_decode_value(v) for v in json.loads(base64.urlsafe_b64decode(padded))]
        if len(values) != expected:
            raise ValueError("wrong arity")
        return values
    except (ValueError, TypeError, KeyError) as exc:
        raise bad_request("invalid_cursor", "Invalid pagination cursor") from exc


async def keyset_page(
    session: AsyncSession,
    stmt: Select,
    key_cols: Sequence[ColumnElement],
    *,
    limit: int | None,
    cursor: str | None,
    descending: bool = False,
) -> tuple[list[Any], str | None]:
    """Run ``stmt`` (selecting one entity) ordered by ``key_cols`` (must end in a unique column).

    Returns ``(entities, next_cursor)``. All key columns share one sort direction.
    """
    limit = clamp_limit(limit)
    labeled = [c.label(f"_k{i}") for i, c in enumerate(key_cols)]
    query = stmt.add_columns(*labeled)
    if cursor:
        values = decode_cursor(cursor, len(key_cols))
        bound = tuple_(*[literal(v, c.type) for c, v in zip(key_cols, values, strict=True)])
        keys = tuple_(*key_cols)
        query = query.where(keys < bound if descending else keys > bound)
    order = [c.desc() if descending else c.asc() for c in key_cols]
    rows = (await session.execute(query.order_by(*order).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        next_cursor = encode_cursor([rows[-1][i + 1] for i in range(len(key_cols))])
    return [r[0] for r in rows], next_cursor
