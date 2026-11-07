from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Attendify"
    environment: str = "development"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "attendify"

    # Must be set to a long random value outside development.
    jwt_secret: str = Field(default="dev-only-change-me", min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 12 * 60

    # Comma-separated list of origins allowed to call the API from a browser (admin panel).
    cors_origins: str = "http://localhost:5173"

    allow_self_registration: bool = True

    # Face recognition. "opencv" = open-source YuNet + SFace (default); "azure" = Azure AI Face.
    face_provider: Literal["opencv", "azure"] = "opencv"
    face_models_dir: str = "models"
    face_match_threshold: float = 0.363  # SFace cosine-similarity threshold recommended by OpenCV
    face_verification_ttl_minutes: int = 10  # a successful face scan unlocks check-in for this long
    max_upload_bytes: int = 5 * 1024 * 1024
    # Azure AI Face (only used when FACE_PROVIDER=azure)
    azure_face_endpoint: str = ""
    azure_face_key: str = ""
    azure_person_group_id: str = "attendify"

    # Attendance rules
    # Time only counts while the phone hears the employee's beacon (see docs/api-contract-v2.md).
    require_bluetooth: bool = True
    bluetooth_min_rssi: int = -80
    heartbeat_interval_seconds: int = 300  # advice to the app
    heartbeat_grace_minutes: int = 15  # max gap credited between two heartbeats
    max_session_hours: int = 14  # open sessions are capped at this length in reports

    # Consent and retention (see docs/api-contract-v3-admin.md). Bump CONSENT_VERSION when the
    # text in app/consent.md changes in a way that needs everyone to agree again.
    consent_version: int = 1
    presence_retention_days: int = Field(default=90, ge=1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
