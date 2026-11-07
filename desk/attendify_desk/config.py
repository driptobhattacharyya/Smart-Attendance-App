"""Persistent settings. The device token is encrypted; everything else is plain JSON."""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import DEFAULT_SERVER_URL
from .beacon import BeaconIdentity

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


@dataclass
class Config:
    server_url: str = DEFAULT_SERVER_URL
    device_token: str | None = None
    beacon: BeaconIdentity | None = None
    beacon_name: str | None = None
    employee_name: str | None = None
    heartbeat_interval: int = 60

    @property
    def paired(self) -> bool:
        return bool(self.device_token and self.beacon)


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
        cfg = Config(server_url=raw.get("server_url") or DEFAULT_SERVER_URL)
        cfg.beacon_name = raw.get("beacon_name")
        cfg.employee_name = raw.get("employee_name")
        cfg.heartbeat_interval = int(raw.get("heartbeat_interval") or 60)
        try:
            enc = raw.get("token_enc")
            if enc:
                cfg.device_token = self.protector.unprotect(base64.b64decode(enc)).decode()
            if raw.get("beacon"):
                cfg.beacon = BeaconIdentity.from_json(raw["beacon"])
        except Exception:  # corrupt, or encrypted by another user/machine
            log.warning("Stored credentials could not be read; treating as unpaired")
            cfg.device_token = None
            cfg.beacon = None
        if not cfg.paired:
            cfg.device_token = None
            cfg.beacon = None
        return cfg

    def save(self, cfg: Config) -> None:
        data: dict = {
            "server_url": cfg.server_url,
            "beacon_name": cfg.beacon_name,
            "employee_name": cfg.employee_name,
            "heartbeat_interval": cfg.heartbeat_interval,
            "beacon": cfg.beacon.to_json() if cfg.beacon else None,
        }
        if cfg.device_token:
            enc = self.protector.protect(cfg.device_token.encode())
            data["token_enc"] = base64.b64encode(enc).decode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), "utf-8")
        tmp.replace(self.path)

    def update_beacon(self, beacon: BeaconIdentity, interval: int) -> None:
        cfg = self.load()
        if cfg.paired:
            cfg.beacon = beacon
            cfg.heartbeat_interval = interval
            self.save(cfg)

    def clear_token(self) -> Config:
        cfg = self.load()
        cfg.device_token = None
        cfg.beacon = None
        cfg.employee_name = None
        self.save(cfg)
        return cfg
