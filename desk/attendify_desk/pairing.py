"""Pairing-code handling and the pairing flow."""

from __future__ import annotations

import platform
import re
import socket

from . import __version__
from .api import DeskApi
from .config import Config, ConfigStore

_VALID = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")


def normalize_code(raw: str) -> str:
    """Upper-case, drop non-alphanumerics, cap at 8 chars, insert the dash (XXXX-XXXX)."""
    chars = "".join(c for c in raw.upper() if c.isascii() and c.isalnum())[:8]
    return chars if len(chars) <= 4 else f"{chars[:4]}-{chars[4:]}"


def is_valid_code(code: str) -> bool:
    return bool(_VALID.match(code))


def pair_device(store: ConfigStore, api: DeskApi, raw_code: str) -> Config:
    """Exchange a pairing code for a device token and persist it. Raises ApiError/ValueError."""
    code = normalize_code(raw_code)
    if not is_valid_code(code):
        raise ValueError("Enter the 8-character pairing code (like ABCD-2345).")
    result = api.pair(
        code,
        hostname=socket.gethostname(),
        os_name=f"{platform.system()} {platform.release()}",
        agent_version=__version__,
    )
    cfg = Config(
        server_url=api.base_url,
        device_token=result.device_token,
        secret=result.secret,
        beacon_uuid=result.beacon_uuid,
        beacon_name=result.beacon_name,
        employee_name=result.employee_name,
        heartbeat_interval=result.heartbeat_interval,
    )
    store.save(cfg)
    return cfg
