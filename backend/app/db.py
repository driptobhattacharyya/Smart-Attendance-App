from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from pymongo import ASCENDING, AsyncMongoClient, ReturnDocument

Database = Any  # pymongo AsyncDatabase (or a compatible mock in tests)


def create_client(uri: str) -> AsyncMongoClient:
    return AsyncMongoClient(uri, tz_aware=True)


def get_db(request: Request) -> Database:
    return request.app.state.db


async def ensure_indexes(db: Database) -> None:
    await db.users.create_index("employee_id", unique=True)
    await db.users.create_index("email", unique=True)
    await db.users.create_index("team_id")
    await db.attendance_sessions.create_index([("employee_id", ASCENDING), ("date", ASCENDING)])
    await db.attendance_requests.create_index([("team_id", ASCENDING), ("status", ASCENDING)])
    await db.attendance_requests.create_index("employee_id")
    await db.attendance_sessions.create_index("date")
    await db.beacons.create_index("office_id")
    await db.beacons.create_index("assigned_employee_id")


async def next_employee_id(db: Database) -> str:
    counter = await db.counters.find_one_and_update(
        {"_id": "employee"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"EMP{counter['seq']:04d}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Mongo drivers/mocks may hand back naive datetimes; they are always UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
