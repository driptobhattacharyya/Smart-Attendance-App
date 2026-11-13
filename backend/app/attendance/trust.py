"""Trust signals from the phone (docs/architecture-v5.md section 8).

The server never trusts a client "everything is fine" boolean. It acts on evidence: a location with
accuracy and age, the mock flag, the rooted flag and -- when enabled -- a Play Integrity token that
Google decodes for us.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time as time_module
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx
import jwt

from ..core.config import Settings
from ..core.errors import AppError, bad_request, forbidden
from ..core.security import utcnow
from ..geo import haversine_m
from .rules import PolicyRules
from .schemas import DeviceIn, LocationIn

logger = logging.getLogger("attendify.trust")

FUTURE_SKEW_SECONDS = 60


@dataclass(frozen=True)
class LocationReading:
    lat: float
    lon: float
    accuracy_m: float | None
    mock: bool
    captured_at: datetime | None


class LocationProblem(Exception):
    """A location that must not be used. ``code`` is the API error code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def to_error(self, status: int = 403) -> AppError:
        return AppError(status, self.code, self.message, self.details)


def location_problem(loc: LocationIn, rules: PolicyRules, now: datetime) -> LocationProblem | None:
    """First problem with a location, in the order that matters: mock, stale, inaccurate."""
    if loc.mock:
        return LocationProblem("mock_location", "Mock location detected")
    if loc.captured_at is not None:
        age = (now - loc.captured_at).total_seconds()
        if age > rules.location_max_age_seconds or age < -FUTURE_SKEW_SECONDS:
            return LocationProblem(
                "location_stale",
                "The location fix is too old; wait for a fresh one",
                age_seconds=int(age),
                max_age_seconds=rules.location_max_age_seconds,
            )
    if loc.accuracy_m is not None and loc.accuracy_m > rules.location_max_accuracy_m:
        return LocationProblem(
            "location_inaccurate",
            "The location is not accurate enough",
            accuracy_m=loc.accuracy_m,
            max_accuracy_m=rules.location_max_accuracy_m,
        )
    return None


def distance_to(loc: LocationIn, latitude: float, longitude: float) -> float:
    return haversine_m(loc.lat, loc.lon, latitude, longitude)


# ------------------------------------------------------------------------ device integrity


@dataclass(frozen=True)
class IntegrityVerdict:
    ok: bool
    reason: str = "ok"
    unavailable: bool = False  # the verification service could not be reached


class IntegrityVerifier(Protocol):
    async def verify(self, token: str, nonce: str | None = None) -> IntegrityVerdict: ...


def evaluate_integrity_payload(
    payload: dict[str, Any],
    *,
    package_name: str,
    cert_digests: set[str],
    now: datetime,
    max_age_seconds: int = 600,
    nonce: str | None = None,
) -> IntegrityVerdict:
    """Decide on the ``tokenPayloadExternal`` returned by ``decodeIntegrityToken``."""
    request = payload.get("requestDetails") or {}
    app = payload.get("appIntegrity") or {}
    device = payload.get("deviceIntegrity") or {}
    if nonce is not None and request.get("nonce") != nonce:
        return IntegrityVerdict(False, "nonce_mismatch")
    if request.get("requestPackageName") not in (None, package_name):
        return IntegrityVerdict(False, "wrong_request_package")
    stamp = request.get("timestampMillis")
    if stamp is not None:
        try:
            age = now.timestamp() - int(stamp) / 1000
        except (TypeError, ValueError):
            return IntegrityVerdict(False, "bad_timestamp")
        if age > max_age_seconds or age < -FUTURE_SKEW_SECONDS:
            return IntegrityVerdict(False, "token_not_fresh")
    if app.get("appRecognitionVerdict") != "PLAY_RECOGNIZED":
        return IntegrityVerdict(False, "app_not_recognized")
    if app.get("packageName") != package_name:
        return IntegrityVerdict(False, "wrong_package")
    if cert_digests and not cert_digests & set(app.get("certificateSha256Digest") or []):
        return IntegrityVerdict(False, "wrong_certificate")
    if "MEETS_DEVICE_INTEGRITY" not in (device.get("deviceRecognitionVerdict") or []):
        return IntegrityVerdict(False, "device_integrity_failed")
    return IntegrityVerdict(True)


class GooglePlayIntegrityVerifier:
    """Calls ``decodeIntegrityToken`` with a service account (OAuth2 JWT-bearer flow done by
    hand: PyJWT signs the assertion, httpx performs the two calls)."""

    SCOPE = "https://www.googleapis.com/auth/playintegrity"
    API = "https://playintegrity.googleapis.com/v1/{package}:decodeIntegrityToken"

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        if not settings.play_integrity_package_name or not (
            settings.play_integrity_service_account_json
        ):
            raise RuntimeError(
                "PLAY_INTEGRITY_ENABLED needs PLAY_INTEGRITY_PACKAGE_NAME and "
                "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON"
            )
        self._package = settings.play_integrity_package_name
        self._certs = {
            c.strip() for c in settings.play_integrity_cert_sha256.split(",") if c.strip()
        }
        self._account = json.loads(settings.play_integrity_service_account_json)
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(8.0))
        self._access: tuple[str, float] | None = None

    async def _access_token(self) -> str:
        if self._access and self._access[1] > time_module.time() + 60:
            return self._access[0]
        now = int(time_module.time())
        token_uri = self._account.get("token_uri", "https://oauth2.googleapis.com/token")
        assertion = jwt.encode(
            {
                "iss": self._account["client_email"],
                "scope": self.SCOPE,
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            self._account["private_key"],
            algorithm="RS256",
        )
        response = await self._http.post(
            token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        response.raise_for_status()
        body = response.json()
        self._access = (body["access_token"], time_module.time() + int(body.get("expires_in", 300)))
        return self._access[0]

    async def verify(self, token: str, nonce: str | None = None) -> IntegrityVerdict:
        try:
            access = await self._access_token()
            response = await self._http.post(
                self.API.format(package=self._package),
                json={"integrity_token": token},
                headers={"Authorization": f"Bearer {access}"},
            )
            if response.status_code in (400, 403) and "integrity" in response.text.lower():
                # Google rejected the token itself (malformed / not for this app).
                return IntegrityVerdict(False, "token_rejected")
            response.raise_for_status()
            payload = response.json().get("tokenPayloadExternal") or {}
        except (httpx.HTTPError, KeyError, ValueError, jwt.PyJWTError):
            logger.exception("Play Integrity call failed")
            return IntegrityVerdict(False, "verification_unavailable", unavailable=True)
        return evaluate_integrity_payload(
            payload,
            package_name=self._package,
            cert_digests=self._certs,
            now=utcnow(),
            nonce=nonce,
        )


@dataclass
class FakeIntegrityVerifier:
    """Tests: ``verdicts`` maps a token to its verdict; unknown tokens fail."""

    verdicts: dict[str, IntegrityVerdict] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def verify(self, token: str, nonce: str | None = None) -> IntegrityVerdict:
        self.calls.append(token)
        return self.verdicts.get(token, IntegrityVerdict(False, "unknown_token"))


def build_integrity_verifier(settings: Settings) -> IntegrityVerifier | None:
    return GooglePlayIntegrityVerifier(settings) if settings.play_integrity_enabled else None


async def check_device(
    device: DeviceIn | None,
    *,
    block_rooted: bool,
    verifier: IntegrityVerifier | None,
    nonce_ok: Callable[[str], Awaitable[bool]] | None = None,
) -> str:
    """Raises 403 ``rooted_device`` / ``integrity_failed`` or 503; returns the integrity label
    stored with the check-in: ``verified``, ``unverified`` (feature off) or ``not_applicable``."""
    if device is not None and device.rooted and block_rooted:
        raise forbidden("Rooted devices are not allowed", "rooted_device")
    if verifier is None:
        return "unverified"
    if device is None or device.platform.lower() != "android":
        return "not_applicable"  # Play Integrity exists on Android only
    if not device.integrity_token:
        raise forbidden("A device integrity token is required", "integrity_required")
    if not device.integrity_nonce or (nonce_ok and not await nonce_ok(device.integrity_nonce)):
        raise forbidden(
            "The integrity nonce is missing, used or expired",
            "integrity_failed",
            reason="bad_nonce",
        )
    verdict = await verifier.verify(device.integrity_token, device.integrity_nonce)
    if verdict.unavailable:
        raise AppError(
            503, "integrity_unavailable", "Device verification is temporarily unavailable"
        )
    if not verdict.ok:
        raise forbidden("Device integrity check failed", "integrity_failed", reason=verdict.reason)
    return "verified"


def require_location(loc: LocationIn | None) -> LocationIn:
    if loc is None:
        raise bad_request("location_required", "A location is required for this check-in")
    return loc


# ------------------------------------------------------------------- integrity nonces

NONCE_TTL_SECONDS = 300


def _nonce_mac(settings: Settings, user_id: Any, nonce_id: bytes, iat: bytes) -> bytes:
    key = hashlib.sha256(b"attendify-integrity-nonce|" + settings.jwt_secret.encode()).digest()
    msg = str(user_id).encode() + b"|" + nonce_id + iat
    return hmac.new(key, msg, hashlib.sha256).digest()[:16]


def issue_nonce(settings: Settings, user_id: Any, now: datetime) -> str:
    """Web-safe base64 (what Play Integrity accepts): id(16) | issued-at(4) | mac(16). Bound to
    the user; single use is enforced when it is consumed."""
    nonce_id, iat = os.urandom(16), int(now.timestamp()).to_bytes(4, "big")
    raw = nonce_id + iat + _nonce_mac(settings, user_id, nonce_id, iat)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def consume_nonce(ctx: Any, nonce: str, now: datetime) -> bool:
    """True exactly once per valid, unexpired nonce issued to this user."""
    from datetime import UTC

    from sqlalchemy.dialects.postgresql import insert

    from .models import FaceChallengeUsed

    try:
        raw = base64.urlsafe_b64decode(nonce + "=" * (-len(nonce) % 4))
    except ValueError:
        return False
    if len(raw) != 36:
        return False
    nonce_id, iat, mac = raw[:16], raw[16:20], raw[20:]
    if not hmac.compare_digest(mac, _nonce_mac(ctx.settings, ctx.user_id, nonce_id, iat)):
        return False
    issued = int.from_bytes(iat, "big")
    if not -FUTURE_SKEW_SECONDS <= now.timestamp() - issued <= NONCE_TTL_SECONDS:
        return False
    row = (
        await ctx.db.execute(
            insert(FaceChallengeUsed)
            .values(
                org_id=ctx.org_id,
                challenge_id="nonce:" + nonce_id.hex(),
                user_id=ctx.user_id,
                expires_at=datetime.fromtimestamp(issued + NONCE_TTL_SECONDS, UTC),
            )
            .on_conflict_do_nothing()
            .returning(FaceChallengeUsed.challenge_id)
        )
    ).first()
    return row is not None
