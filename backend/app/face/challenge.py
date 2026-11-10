"""Stateless, signed liveness challenges.

Flow: ``GET /face/challenge`` -> ``ch = new_challenge()``; ``token = sign_challenge(ch, key)``
is returned to the app. ``POST /face/verify`` sends the token back; the API calls
``verify_challenge_token(token, key, subject=user_id)`` and hands the resulting ``Challenge`` to
``provider.verify``.

SINGLE USE IS THE CALLER'S JOB. The token is stateless so the server cannot know it was already
used. After a verify attempt (pass or fail) the API MUST record ``challenge.id`` until
``issued_at + expires_in`` (a unique-key insert in Postgres, or a short-TTL cache) and reject a
second use. Without that, a recorded successful session could be replayed within 60 s.
Binding ``subject`` to the user id stops one employee reusing another's challenge.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import time
import uuid

from .errors import ChallengeError
from .types import ACTIONS, Challenge

CLOCK_SKEW_S = 5


def new_challenge(
    rng: random.Random | None = None,
    *,
    actions: tuple[str, ...] = ("turn_left", "turn_right"),
    expires_in: int = 60,
    subject: str | None = None,
    now: float | None = None,
) -> Challenge:
    """Pick a random action. Production uses the default ``random.SystemRandom()``; a seeded
    ``random.Random`` is for tests only."""
    rng = rng or random.SystemRandom()
    bad = [a for a in actions if a not in ACTIONS]
    if not actions or bad:
        raise ValueError(f"invalid challenge actions: {actions!r}")
    return Challenge(
        id=uuid.UUID(int=rng.getrandbits(128), version=4).hex,
        action=rng.choice(list(actions)),
        issued_at=time.time() if now is None else now,
        expires_in=expires_in,
        subject=subject,
    )


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key(key: bytes | str) -> bytes:
    k = key.encode() if isinstance(key, str) else key
    if not k:
        raise ValueError("challenge signing key must not be empty")
    return k


def _mac(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, b"attendify-face-challenge-v1." + payload, hashlib.sha256).digest()


def sign_challenge(ch: Challenge, key: bytes | str) -> str:
    payload = json.dumps(
        {"id": ch.id, "a": ch.action, "iat": ch.issued_at, "exp": ch.expires_in, "sub": ch.subject},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"{_b64e(payload)}.{_b64e(_mac(_key(key), payload))}"


def verify_challenge_token(
    token: str,
    key: bytes | str,
    *,
    subject: str | None = None,
    now: float | None = None,
) -> Challenge:
    """Return the Challenge or raise ChallengeError. Does NOT enforce single use."""
    k = _key(key)
    try:
        body, sig = token.split(".")
        payload = _b64d(body)
        given = _b64d(sig)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ChallengeError("malformed") from exc
    if not hmac.compare_digest(_mac(k, payload), given):
        raise ChallengeError("bad_signature")
    try:
        d = json.loads(payload)
        ch = Challenge(
            id=str(d["id"]),
            action=str(d["a"]),
            issued_at=float(d["iat"]),
            expires_in=int(d["exp"]),
            subject=d.get("sub"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ChallengeError("malformed") from exc
    if ch.action not in ACTIONS:
        raise ChallengeError("malformed")
    t = time.time() if now is None else now
    if ch.issued_at > t + CLOCK_SKEW_S:
        raise ChallengeError("not_yet_valid")
    if ch.is_expired(t):
        raise ChallengeError("expired")
    if ch.subject is not None and ch.subject != subject:
        raise ChallengeError("wrong_subject")
    return ch
