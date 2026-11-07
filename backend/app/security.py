from datetime import timedelta
from enum import StrEnum

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from .config import Settings, get_settings
from .db import Database, get_db, utcnow


class Role(StrEnum):
    employee = "employee"
    manager = "manager"
    admin = "admin"


_password_hash = PasswordHash.recommended()
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


def create_access_token(employee_id: str, role: str, settings: Settings) -> str:
    now = utcnow()
    payload = {
        "sub": employee_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if credentials is None:
        raise _unauthorized()
    try:
        payload = jwt.decode(
            credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Session expired, please log in again") from exc
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid token") from exc

    user = await db.users.find_one({"employee_id": payload.get("sub")})
    if not user or not user.get("active", True):
        raise _unauthorized("User not found or disabled")
    return user


def require_role(*roles: Role):
    allowed = {r.value for r in roles}

    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this action")
        return user

    return dependency


require_manager = require_role(Role.manager, Role.admin)
require_admin = require_role(Role.admin)
