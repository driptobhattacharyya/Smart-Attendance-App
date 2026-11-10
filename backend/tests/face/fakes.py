"""Test doubles for the face engine and spoof detector.

A "frame" is a JSON payload: who (identity), yaw (degrees), seed (pixel noise, so different
frames differ like real camera frames), faces (how many faces), eyes (openness), spoof (P(real)).
The fake engine turns it into a sharp, mid-brightness synthetic image and matching landmarks.
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np

from app.face.errors import FaceError
from app.face.types import FaceObs

BBOX = (40.0, 40.0, 120.0, 120.0)
EYE_DIST = 60.0


def landmarks_for(yaw_deg: float, ratio: float = 0.5) -> np.ndarray:
    cx, cy = 100.0, 90.0
    nose_dx = EYE_DIST * ratio * math.sin(math.radians(yaw_deg))
    return np.array(
        [
            [cx - EYE_DIST / 2, cy],
            [cx + EYE_DIST / 2, cy],
            [cx + nose_dx, cy + 30],
            [cx - 20, cy + 60],
            [cx + 20, cy + 60],
        ]
    )


def frame(who="alice", yaw=0.0, seed=0, faces=1, eyes=1.0, spoof=0.95, **extra) -> bytes:
    return json.dumps(
        {
            "who": who,
            "yaw": yaw,
            "seed": seed,
            "faces": faces,
            "eyes": eyes,
            "spoof": spoof,
            **extra,
        }
    ).encode()


def identity_vec(who: str, seed: int = 0, noise: float = 0.05) -> np.ndarray:
    base = np.random.default_rng(int(hashlib.md5(who.encode()).hexdigest()[:8], 16)).normal(
        size=128
    )
    base /= np.linalg.norm(base)
    jitter = np.random.default_rng(seed + 1000).normal(size=128) * noise / math.sqrt(128)
    return (base + jitter).astype(np.float32)


class FakeEngine:
    model_id = "fake"

    def __init__(self):
        self.payloads: dict[int, dict] = {}

    def decode(self, data: bytes) -> np.ndarray:
        try:
            p = json.loads(bytes(data))
        except ValueError as exc:
            raise FaceError("unreadable") from exc
        rng = np.random.default_rng(p["seed"])
        img = rng.integers(60, 200, size=(200, 200, 3), dtype=np.uint8)
        self.payloads[id(img)] = p
        img[0, 0, 0] = p["seed"] % 256  # make the payload recoverable in embed/detect
        self._last = p
        return img

    def _payload(self, img):
        return self.payloads[id(img)]

    def detect(self, img):
        p = self._payload(img)
        return [FaceObs(BBOX, landmarks_for(p["yaw"]), 0.99) for _ in range(p["faces"])]

    def embed(self, img, face):
        p = self._payload(img)
        return identity_vec(p["who"], p["seed"])

    def eye_openness(self, img, face):
        return self._payload(img)["eyes"]


class FakeSpoof:
    """Returns the per-frame ``spoof`` value stored in the payload."""

    def __init__(self, engine: FakeEngine):
        self._engine = engine

    def score(self, img, face) -> float:
        return float(self._engine._payload(img)["spoof"])
