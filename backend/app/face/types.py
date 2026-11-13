"""Plain data types exchanged with the API layer. No database or FastAPI dependencies."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

ACTIONS = ("turn_left", "turn_right", "blink")


@dataclass(frozen=True)
class Challenge:
    id: str
    action: str  # one of ACTIONS
    issued_at: float  # unix seconds
    expires_in: int = 60
    subject: str | None = None  # optional binding to a user id (prevents cross-user reuse)

    def is_expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) > self.issued_at + self.expires_in


@dataclass(frozen=True)
class EnrolledFace:
    """What gets stored for an employee. Never contains an image."""

    provider: str  # "opencv" | "azure"
    embedding: list[float] | None = None  # L2-normalised mean embedding (opencv)
    external_id: str | None = None  # e.g. Azure person id
    model: str = ""  # recogniser identifier, so re-enrolment can be forced on model change
    frames_used: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "embedding": self.embedding,
            "external_id": self.external_id,
            "model": self.model,
            "frames_used": self.frames_used,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EnrolledFace:
        return cls(
            provider=d["provider"],
            embedding=d.get("embedding"),
            external_id=d.get("external_id"),
            model=d.get("model", ""),
            frames_used=d.get("frames_used", 1),
        )


@dataclass(frozen=True)
class VerifyResult:
    verified: bool
    score: float  # mean cosine similarity to the enrolled face (0 if not reached)
    liveness_score: float  # 0..1; passive spoof score if the model ran, else active-check strength
    checks: dict[str, Any] = field(default_factory=dict)  # per-check detail, safe to log/store
    reason: str = "ok"  # "ok" or the first failing check's code


@dataclass(frozen=True)
class FaceObs:
    """One detected face. ``landmarks`` is 5x2: right eye, left eye, nose, right/left mouth
    corner (YuNet order; "right" = the subject's right = image-left in an unmirrored frame)."""

    bbox: tuple[float, float, float, float]  # x, y, w, h in pixels
    landmarks: Any  # numpy array (5, 2)
    score: float = 1.0
