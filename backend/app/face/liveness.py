"""Liveness building blocks. Everything here is pure (numpy in, numbers out) and unit-testable
without models.

Reliability, honestly:

* Head yaw from YuNet's 5 landmarks is a *geometric estimate* (nose offset from the eye
  midpoint, along the eye axis, divided by eye distance, through ``asin``). It is roll-invariant
  and good to a few degrees for frontal-ish faces; it is not a real pose solver. We only use it
  as a *change* between two frames, with a generous threshold.
* A flat photo rotated in front of the camera also changes the apparent landmark geometry a bit,
  which is why this active check is paired with the passive model and the identity checks.
  Active turn alone is NOT proof of a live person.
* Blink needs an eye-openness measure. YuNet's landmarks give only eye *centres*, so there is no
  eye aspect ratio. The Haar eye cascade is not shipped in opencv-python wheels (verified:
  ``cv2.data`` is empty), and blinks last ~150 ms so a 3-frame burst rarely contains one.
  Blink is therefore implemented against an injectable ``eye_openness`` but is NOT in the
  default challenge set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import FaceConfig
from .types import FaceObs

# ----------------------------------------------------------------------------- yaw


def yaw_from_landmarks(landmarks: np.ndarray, cfg: FaceConfig | None = None) -> float:
    """Estimated head yaw in degrees. Positive = subject turned toward THEIR LEFT
    (nose moves to image-right in an unmirrored frame); negative = toward their right.

    ``landmarks``: 5x2, YuNet order (right eye, left eye, nose, right mouth, left mouth).
    """
    cfg = cfg or FaceConfig()
    lm = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    re, le, nose = lm[0], lm[1], lm[2]
    axis = le - re
    dist = float(np.linalg.norm(axis))
    if dist < 1e-6:
        raise ValueError("degenerate landmarks (eyes coincide)")
    mid = (re + le) / 2.0
    offset = float(np.dot(nose - mid, axis / dist)) / dist  # in eye-distances
    ratio = max(-1.0, min(1.0, offset / cfg.yaw_nose_ratio))
    yaw = math.degrees(math.asin(ratio))
    return -yaw if cfg.mirrored_frames else yaw


# ----------------------------------------------------------------------------- quality


@dataclass(frozen=True)
class QualityReport:
    ok: bool
    reason: str  # "ok" | face_too_small | too_dark | too_bright | too_blurry
    metrics: dict[str, float] = field(default_factory=dict)


def _clip_box(bbox: tuple[float, float, float, float], shape: tuple[int, ...]):
    h, w = shape[:2]
    x, y, bw, bh = bbox
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(w, int(x + bw)), min(h, int(y + bh))
    return x0, y0, x1, y1


def face_gray(img: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray:
    x0, y0, x1, y1 = _clip_box(bbox, img.shape)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    return crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)


def brightness(gray: np.ndarray) -> float:
    return float(np.mean(gray))


def blur_variance(gray: np.ndarray) -> float:
    """Variance of the Laplacian after resizing to 112x112 so it is scale-independent."""
    g = cv2.resize(gray, (112, 112), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def check_quality(img: np.ndarray, face: FaceObs, cfg: FaceConfig) -> QualityReport:
    """Gates on the (already single) detected face: size, brightness, blur."""
    _, _, w, h = face.bbox
    gray = face_gray(img, face.bbox)
    m = {
        "face_px": float(min(w, h)),
        "brightness": brightness(gray),
        "blur_variance": blur_variance(gray),
    }
    if m["face_px"] < cfg.min_face_px:
        return QualityReport(False, "face_too_small", m)
    if m["brightness"] < cfg.min_brightness:
        return QualityReport(False, "too_dark", m)
    if m["brightness"] > cfg.max_brightness:
        return QualityReport(False, "too_bright", m)
    if m["blur_variance"] < cfg.min_blur_variance:
        return QualityReport(False, "too_blurry", m)
    return QualityReport(True, "ok", m)


def thumbnail(img: np.ndarray) -> np.ndarray:
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)


def frames_are_static(thumbs: list[np.ndarray], cfg: FaceConfig) -> bool:
    """True if every frame is (almost) pixel-identical to the first: a replayed still or a
    client re-sending one image. Real camera frames always differ by sensor noise."""
    if len(thumbs) < 2:
        return False
    first = thumbs[0]
    return all(float(np.mean(np.abs(t - first))) < cfg.static_frame_mad for t in thumbs[1:])


# ----------------------------------------------------------------------------- identity


def pairwise_min_similarity(embeddings: list[np.ndarray]) -> float:
    """Smallest cosine similarity between any two embeddings (1.0 for a single one)."""
    e = [np.asarray(x, dtype=np.float32).ravel() for x in embeddings]
    e = [x / (np.linalg.norm(x) or 1.0) for x in e]
    lowest = 1.0
    for i in range(len(e)):
        for j in range(i + 1, len(e)):
            lowest = min(lowest, float(np.dot(e[i], e[j])))
    return lowest


# ----------------------------------------------------------------------------- active check


@dataclass(frozen=True)
class ActiveResult:
    passed: bool
    strength: float  # 0..1, how far past the threshold (for liveness_score)
    detail: dict[str, object]


def evaluate_turn(yaws: list[float], action: str, cfg: FaceConfig) -> ActiveResult:
    """frames[0] must be roughly frontal; some later frame must have turned by more than
    ``min_yaw_change_deg`` in the requested direction relative to it."""
    sign = 1.0 if action == "turn_left" else -1.0
    base = yaws[0]
    delta = max(sign * (y - base) for y in yaws[1:]) if len(yaws) > 1 else 0.0
    frontal = abs(base) <= cfg.max_baseline_yaw_deg
    passed = frontal and delta > cfg.min_yaw_change_deg
    strength = max(0.0, min(1.0, delta / (2.0 * cfg.min_yaw_change_deg)))
    detail = {
        "yaws": [round(y, 1) for y in yaws],
        "baseline_frontal": frontal,
        "directional_change_deg": round(delta, 1),
        "required_deg": cfg.min_yaw_change_deg,
    }
    return ActiveResult(passed, strength if passed else min(strength, 0.49), detail)


def evaluate_blink(openness: list[float | None], cfg: FaceConfig) -> ActiveResult:
    """Open eyes in the first frame, then a frame with eyes closed. ``None`` = no measure."""
    if any(o is None for o in openness):
        return ActiveResult(False, 0.0, {"error": "eye_openness_unavailable"})
    vals = [float(o) for o in openness if o is not None]
    opened = vals[0] >= cfg.blink_open_min
    closed = any(v <= cfg.blink_closed_max for v in vals[1:])
    return ActiveResult(opened and closed, 1.0 if opened and closed else 0.0, {"openness": vals})


def evaluate_action(
    action: str,
    yaws: list[float],
    openness: list[float | None],
    cfg: FaceConfig,
) -> ActiveResult:
    if action in ("turn_left", "turn_right"):
        return evaluate_turn(yaws, action, cfg)
    if action == "blink":
        return evaluate_blink(openness, cfg)
    return ActiveResult(False, 0.0, {"error": "unknown_action"})


# ----------------------------------------------------------------------------- passive


@dataclass(frozen=True)
class PassiveResult:
    passed: bool
    score: float  # mean P(real)
    detail: dict[str, object]


def evaluate_passive(scores: list[float], cfg: FaceConfig) -> PassiveResult:
    mean = float(np.mean(scores))
    lowest = float(np.min(scores))
    passed = mean >= cfg.passive_threshold and lowest >= cfg.passive_min_frame
    return PassiveResult(
        passed, mean, {"scores": [round(s, 3) for s in scores], "mean": round(mean, 3)}
    )
