"""Persistent settings. The device token and identity secret are encrypted; the rest is JSON."""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import DEFAULT_SERVER_URL

log = logging.getLogger(__name__)


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


class FakeProtector:
    """Reversible stand-in for tests and non-Windows runs. NOT secure."""

    def protect(self, data: bytes) -> bytes:
        return b"fake:" + data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"fake:"):
            raise ValueError("not protected by FakeProtector")
        return data[5:][::-1]


def default_protector() -> Protector:
    if os.name == "nt":
        from .dpapi import DpapiProtector

        return DpapiProtector()
    return FakeProtector()


def migrate_server_url(url: str) -> str:
    """Versions up to 1.0 stored `.../api`; the API now lives under `.../api/v1`."""
    base = url.strip().rstrip("/")
    if base.endswith("/api"):
        return base + "/v1"
    return base


@dataclass
class Config:
    server_url: str = DEFAULT_SERVER_URL
    # Secrets are excluded from repr so a traceback or debug print cannot leak them.
    device_token: str | None = field(default=None, repr=False)
    secret: bytes | None = field(default=None, repr=False)  # 32 bytes: the rotating identity key
    beacon_uuid: str | None = None  # the org's desk UUID, lowercase
    beacon_name: str | None = None
    employee_name: str | None = None
    heartbeat_interval: int = 60

    @property
    def paired(self) -> bool:
        return bool(self.device_token and self.secret and self.beacon_uuid)


def default_config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Attendify Desk" / "config.json"


class ConfigStore:
    def __init__(self, path: Path, protector: Protector):
        self.path = path
        self.protector = protector

    def load(self) -> Config:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return Config()
        cfg = Config(server_url=migrate_server_url(raw.get("server_url") or DEFAULT_SERVER_URL))
        cfg.beacon_uuid = raw.get("beacon_uuid")
        cfg.beacon_name = raw.get("beacon_name")
        cfg.employee_name = raw.get("employee_name")
        cfg.heartbeat_interval = int(raw.get("heartbeat_interval") or 60)
        try:
            enc = raw.get("token_enc")
            if enc:
                cfg.device_token = self.protector.unprotect(base64.b64decode(enc)).decode()
            senc = raw.get("secret_enc")
            if senc:
                cfg.secret = self.protector.unprotect(base64.b64decode(senc))
        except Exception:  # corrupt, or encrypted by another user/machine
            log.warning("Stored credentials could not be read; treating as unpaired")
            cfg.device_token = None
            cfg.secret = None
        if not cfg.paired:
            # Also covers configs written before rotating identities (no secret): re-pair.
            cfg.device_token = None
            cfg.secret = None
            cfg.beacon_uuid = None
        return cfg

    def save(self, cfg: Config) -> None:
        data: dict = {
            "server_url": cfg.server_url,
            "beacon_uuid": cfg.beacon_uuid,
            "beacon_name": cfg.beacon_name,
            "employee_name": cfg.employee_name,
            "heartbeat_interval": cfg.heartbeat_interval,
        }
        if cfg.device_token:
            enc = self.protector.protect(cfg.device_token.encode())
            data["token_enc"] = base64.b64encode(enc).decode()
        if cfg.secret:
            enc = self.protector.protect(cfg.secret)
            data["secret_enc"] = base64.b64encode(enc).decode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), "utf-8")
        tmp.replace(self.path)

    def update_interval(self, interval: int) -> None:
        cfg = self.load()
        if cfg.paired:
            cfg.heartbeat_interval = interval
            self.save(cfg)

    def clear_token(self) -> Config:
        cfg = self.load()
        cfg.device_token = None
        cfg.secret = None
        cfg.beacon_uuid = None
        cfg.employee_name = None
        self.save(cfg)
        return cfg
