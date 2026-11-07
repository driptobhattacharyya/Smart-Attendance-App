from functools import lru_cache

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

    # Face recognition (OpenCV YuNet + SFace)
    face_models_dir: str = "models"
    face_match_threshold: float = 0.363  # SFace cosine-similarity threshold recommended by OpenCV
    face_verification_ttl_minutes: int = 10  # a successful face scan unlocks check-in for this long
    max_upload_bytes: int = 5 * 1024 * 1024

    # Attendance rules
    require_bluetooth: bool = False
    bluetooth_min_rssi: int = -80
    max_session_hours: int = 14  # open sessions are capped at this length in reports

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
