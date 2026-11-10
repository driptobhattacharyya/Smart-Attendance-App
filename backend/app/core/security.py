"""Password hashing, access tokens and opaque random tokens."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from .config import Settings
from .errors import unauthorized

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128


def utcnow() -> datetime:
    return datetime.now(UTC)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_token(prefix: str) -> str:
    """An opaque bearer secret: ``prefix`` + 32 random bytes. Store only ``sha256_hex`` of it."""
    return prefix + secrets.token_urlsafe(32)


class PasswordHasher:
    def __init__(self, settings: Settings) -> None:
        self._hash = PasswordHash(
            (
                Argon2Hasher(
                    time_cost=settings.argon2_time_cost,
                    memory_cost=settings.argon2_memory_cost,
                ),
            )
        )
        self._dummy = self._hash.hash("attendify-dummy-password")

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hash.hash, password)

    async def verify(self, password: str, hashed: str | None) -> bool:
        """Constant work even when there is no hash, so unknown users cost the same."""
        if not hashed:
            await asyncio.to_thread(self._hash.verify, password, self._dummy)
            return False
        return await asyncio.to_thread(self._hash.verify, password, hashed)


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    org_id: uuid.UUID
    session_id: uuid.UUID


def create_access_token(settings: Settings, claims: AccessClaims) -> tuple[str, int]:
    now = utcnow()
    expires = timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(claims.user_id),
        "org": str(claims.org_id),
        "sid": str(claims.session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
        "typ": "access",
    }
    return (
        jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm),
        int(expires.total_seconds()),
    )


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "org", "sid"]},
        )
        if payload.get("typ") != "access":
            raise ValueError("wrong token type")
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            org_id=uuid.UUID(payload["org"]),
            session_id=uuid.UUID(payload["sid"]),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise unauthorized("Invalid or expired token", "invalid_token") from exc
