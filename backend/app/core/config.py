"""All configuration comes from environment variables (or a local .env file)."""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-change-me-dev-only-change-me"
# 32 zero bytes. Only acceptable in development/tests.
DEV_DATA_KEY = base64.b64encode(bytes(32)).decode()


def asyncpg_url(url: str) -> str:
    """Accept the usual ``postgresql://`` form and make it use the asyncpg driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Attendify"
    environment: str = "development"

    # --- database -------------------------------------------------------------------------
    # Request handlers connect as ``attendify_app`` (subject to row level security).
    database_url: str = "postgresql+asyncpg://attendify_app@localhost:5432/attendify"
    # The BYPASSRLS role, used only by the small named set of functions (see core/db.py).
    database_system_url: str = "postgresql+asyncpg://attendify_system@localhost:5432/attendify"
    # Table owner, used by Alembic only. Falls back to DATABASE_URL when empty.
    database_owner_url: str = ""
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    # No client-side pool at all: for serverless runtimes in front of a transaction pooler.
    db_null_pool: bool = False

    # --- secrets --------------------------------------------------------------------------
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=16)
    jwt_algorithm: str = "HS256"
    # base64 of 32 random bytes; encrypts per-device secrets at rest (AES-GCM).
    data_key: str = DEV_DATA_KEY
    # Shared secret for /api/v1/internal/* scheduled-job endpoints (EventBridge).
    internal_job_secret: str = ""

    # --- tokens ---------------------------------------------------------------------------
    access_token_minutes: int = Field(default=15, ge=1)
    refresh_token_days: int = Field(default=30, ge=1)
    reset_token_minutes: int = Field(default=60, ge=1)
    invite_token_days: int = Field(default=7, ge=1)
    # Argon2 cost. The defaults are pwdlib's; tests lower them for speed.
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=32)

    # --- rate limiting (Postgres backed, see auth/ratelimit.py) ----------------------------
    login_max_failures: int = Field(default=10, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    forgot_max_requests: int = Field(default=5, ge=1)
    signup_max_per_hour: int = Field(default=10, ge=1)
    pair_max_failures: int = Field(default=10, ge=1)

    # --- email ----------------------------------------------------------------------------
    email_backend: Literal["console", "capture"] = "console"
    email_from: str = "Attendify <no-reply@attendify.local>"
    # Base URL of the admin web app; reset/invite emails link to it.
    public_app_url: str = "http://localhost:5173"

    # --- product defaults -----------------------------------------------------------------
    cors_origins: str = "http://localhost:5173"
    allow_signup: bool = True
    default_max_employees: int = Field(default=50, ge=1)
    desk_pairing_ttl_minutes: int = Field(default=15, ge=1)
    desk_heartbeat_interval_seconds: int = Field(default=60, ge=1)
    presence_retention_days: int = Field(default=90, ge=1)
    # Used only to word the consent text; the face package owns the real configuration.
    face_provider: Literal["opencv", "azure"] = "opencv"

    @field_validator("database_url", "database_system_url", "database_owner_url")
    @classmethod
    def _driver(cls, value: str) -> str:
        return asyncpg_url(value)

    @field_validator("data_key")
    @classmethod
    def _data_key(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError("DATA_KEY must be base64") from exc
        if len(raw) != 32:
            raise ValueError("DATA_KEY must decode to exactly 32 bytes")
        return value

    @property
    def data_key_bytes(self) -> bytes:
        return base64.b64decode(self.data_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_development(self) -> bool:
        return self.environment in ("development", "test")

    def assert_production_ready(self) -> None:
        if self.is_development:
            return
        if self.jwt_secret == DEV_JWT_SECRET:
            raise RuntimeError("Set JWT_SECRET to a long random value outside development")
        if self.data_key == DEV_DATA_KEY:
            raise RuntimeError("Set DATA_KEY (base64 of 32 random bytes) outside development")


@lru_cache
def get_settings() -> Settings:
    return Settings()
