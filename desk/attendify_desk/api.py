"""HTTP client for the desk endpoints (docs/api-contract-v4-desk-agent.md)."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import logging
import uuid as uuidlib
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

SECRET_BYTES = 32


class ApiError(Exception):
    """The server answered, but not with what we need."""


class InvalidCode(ApiError):
    pass


class Unpaired(ApiError):
    """401 on heartbeat: the token is no longer valid."""


class ServerUnreachable(ApiError):
    """Network failure or 5xx; retry later."""


@dataclass(repr=False)
class PairResult:
    device_token: str
    secret: bytes  # never log this; it is the key for the rotating identity
    beacon_uuid: str  # the org's desk UUID, lowercase
    beacon_name: str
    employee_name: str | None
    heartbeat_interval: int

    def __repr__(self) -> str:  # keep secrets out of tracebacks and debug output
        return f"PairResult(beacon_uuid={self.beacon_uuid!r}, beacon_name={self.beacon_name!r})"


@dataclass
class HeartbeatResult:
    heartbeat_interval: int
    server_unix: float | None  # the server's clock at the reply, for skew estimation


class DeskApi:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.base_url = base_url.strip().rstrip("/")
        self._client = client or httpx.Client(timeout=15.0)

    def _post(self, path: str, json: dict, headers: dict | None = None) -> httpx.Response:
        try:
            return self._client.post(self.base_url + path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise ServerUnreachable(str(exc) or type(exc).__name__) from exc

    def pair(self, code: str, hostname: str, os_name: str, agent_version: str) -> PairResult:
        r = self._post(
            "/desk/pair",
            {"code": code, "hostname": hostname, "os": os_name, "agent_version": agent_version},
        )
        if r.status_code == 404:
            raise InvalidCode("Invalid or expired code")
        if r.status_code == 429:
            raise ApiError("Too many attempts. Wait a minute and try again.")
        if r.status_code >= 500:
            raise ServerUnreachable(f"Server error {r.status_code}")
        if r.status_code not in (200, 201):
            raise ApiError(f"Unexpected response {r.status_code}")
        try:
            d = r.json()
            b = d["beacon"]
            emp = d.get("employee")
            secret = base64.b64decode(d["secret"], validate=True)
            if len(secret) != SECRET_BYTES:
                raise ValueError("secret has the wrong length")
            return PairResult(
                device_token=d["device_token"],
                secret=secret,
                beacon_uuid=str(uuidlib.UUID(str(b["uuid"]))),
                beacon_name=b.get("name") or "this desk",
                employee_name=emp.get("name") if emp else None,
                heartbeat_interval=int(d.get("heartbeat_interval_seconds", 60)),
            )
        except (KeyError, ValueError, TypeError, AttributeError, binascii.Error) as exc:
            # Message only: the reply body may contain the secret.
            raise ApiError("Unexpected response from server") from exc

    def heartbeat(
        self, token: str, locked: bool, idle_seconds: int, advertising: bool, agent_version: str
    ) -> HeartbeatResult:
        r = self._post(
            "/desk/heartbeat",
            {
                "locked": locked,
                "idle_seconds": idle_seconds,
                "advertising": advertising,
                "agent_version": agent_version,
            },
            headers={"Authorization": f"Device {token}"},
        )
        if r.status_code == 401:
            raise Unpaired("unpaired")
        if r.status_code >= 500:
            raise ServerUnreachable(f"Server error {r.status_code}")
        if r.status_code != 200:
            raise ApiError(f"Unexpected response {r.status_code}")
        try:
            d = r.json()
            if d.get("paired") is False:
                raise Unpaired("unpaired")
            # major/minor are no longer sent (rotating identity); any "beacon" key is ignored.
            return HeartbeatResult(
                heartbeat_interval=int(d.get("heartbeat_interval_seconds", 60)),
                server_unix=_parse_server_time(d.get("server_time")),
            )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ApiError("Unexpected response from server") from exc


def _parse_server_time(value: object) -> float | None:
    """ISO-8601 UTC from the server. A missing or malformed value only disables skew correction."""
    if not isinstance(value, str):
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Ignoring malformed server_time")
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return t.timestamp()
