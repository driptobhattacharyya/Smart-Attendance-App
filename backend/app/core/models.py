"""Declarative base shared by every package's ORM models.

The schema itself is owned by Alembic (hand-written migrations); these classes only describe
the tables for queries.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
