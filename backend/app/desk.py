"""Attendify Desk: pairing codes, device tokens and beacon identity allocation.

See docs/api-contract-v4-desk-agent.md. Codes and tokens are only ever stored as sha256 hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .db import Database, get_db

# No 0/O/1/I/L: nothing that can be misread when typed from a screen.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
MAX_MAJOR = MAX_MINOR = 65535
ALLOCATION_ATTEMPTS = 20


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def secrets_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize_code(value: str) -> str:
    """What the admin read out, however the person typed it: upper case, no dash or spaces."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def new_device_token() -> str:
    return "adt_" + secrets.token_urlsafe(32)


def _identity_from_sequence(n: int) -> tuple[int, int] | None:
    major, minor = divmod(n - 1, MAX_MINOR)
    major += 1
    return (major, minor + 1) if major <= MAX_MAJOR else None


async def insert_desk_beacon(db: Database, doc: dict, auto_identity: bool) -> dict:
    """Insert a desk_app beacon. With ``auto_identity`` (major/minor omitted) a unique
    (major, minor) is drawn from an atomic per-UUID counter; the unique index on
    (uuid, major, minor) is the final guard, and a collision (an admin picked a number by hand)
    just draws the next one. An explicit identity that is taken is a 409."""
    for _ in range(ALLOCATION_ATTEMPTS if auto_identity else 1):
        if auto_identity:
            counter = await db.counters.find_one_and_update(
                {"_id": f"desk_beacon:{doc['uuid']}"},
                {"$inc": {"seq": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            identity = _identity_from_sequence(counter["seq"])
            if identity is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "No free desk identities left")
            doc["major"], doc["minor"] = identity
        try:
            result = await db.beacons.insert_one(doc)
        except DuplicateKeyError:
            doc.pop("_id", None)
            continue
        doc["_id"] = result.inserted_id
        return doc
    raise HTTPException(status.HTTP_409_CONFLICT, "That uuid/major/minor is already in use")


def unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Device not paired",
        headers={"WWW-Authenticate": "Device"},
    )


async def get_desk_beacon(
    authorization: str | None = Header(default=None), db: Database = Depends(get_db)
) -> dict:
    """The desk_app beacon whose device token is in ``Authorization: Device <token>``."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "device" or not token:
        raise unauthorized()
    wanted = hash_secret(token)
    beacon = await db.beacons.find_one({"device_token_hash": wanted})
    if not beacon or not secrets_equal(beacon["device_token_hash"], wanted):
        raise unauthorized()
    return beacon
