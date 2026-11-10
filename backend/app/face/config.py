"""All tunables in one place. Defaults are starting points: calibrate on real devices
(see docs/face-liveness.md) before trusting any of them in production."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class FaceConfig:
    # --- identity ---
    match_threshold: float = 0.363  # SFace cosine threshold recommended by OpenCV (FAR ~ 1e-3)
    # Frames of one session should look very alike; this is far stricter than match_threshold
    # so a face swapped in between frames is caught even if both would match the enrolment.
    same_person_threshold: float = 0.5
    enroll_min_frames: int = 1
    enroll_max_frames: int = 3
    min_frames: int = 2  # verify: frontal frame + at least one later frame
    max_frames: int = 6
    max_frame_bytes: int = 4_000_000  # bounds decode cost / abuse

    # --- detection + quality gates ---
    detector_score_threshold: float = 0.8
    max_image_side: int = 640  # frames are downscaled to this before detection
    min_face_px: int = 80  # shorter side of the face box, in downscaled pixels
    min_brightness: float = 45.0  # mean gray (0..255) inside the face box
    max_brightness: float = 220.0
    min_blur_variance: float = 40.0  # variance of Laplacian on a 112x112 gray face crop

    # --- active liveness ---
    challenge_actions: tuple[str, ...] = ("turn_left", "turn_right")  # blink: see docs
    challenge_ttl_s: int = 60
    min_yaw_change_deg: float = 15.0  # required change in the right direction
    max_baseline_yaw_deg: float = 15.0  # first frame must be roughly frontal
    yaw_nose_ratio: float = 0.5  # nose offset / eye distance at 90 degrees (anatomical ~0.45-0.5)
    mirrored_frames: bool = False  # True if the app mirrors frames (front camera selfie view)
    static_frame_mad: float = 0.5  # mean abs diff (0..255, 64x64 gray) below which frames = same
    blink_closed_max: float = 0.0  # eye openness (0..1) at or below this counts as closed
    blink_open_min: float = 0.5

    # --- passive anti-spoofing (MiniFASNet) ---
    passive_mode: str = "auto"  # auto | on | off
    passive_threshold: float = 0.70  # mean P(real) over frames must reach this
    passive_min_frame: float = 0.40  # and no single frame may fall below this
    spoof_divide_255: bool = False  # original repo feeds raw 0..255 BGR; flip if the ONNX export
    # you use was trained with /255 inputs.
    models_dir: str = "models"
    eye_cascade_path: str = ""  # optional Haar eye cascade for blink (not shipped in wheels)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FaceConfig:
        """Override any field with FACE_<FIELD_UPPER>, e.g. FACE_MIN_YAW_CHANGE_DEG=20.
        ``FACE_PASSIVE_LIVENESS`` is the documented name for ``passive_mode``."""
        env = os.environ if env is None else env
        kwargs: dict[str, object] = {}
        for f in fields(cls):
            key = {"passive_mode": "FACE_PASSIVE_LIVENESS"}.get(f.name, f"FACE_{f.name.upper()}")
            if key not in env:
                continue
            raw = env[key]
            default = getattr(cls, f.name)
            if isinstance(default, bool):
                kwargs[f.name] = raw.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                kwargs[f.name] = int(raw)
            elif isinstance(default, float):
                kwargs[f.name] = float(raw)
            elif isinstance(default, tuple):
                kwargs[f.name] = tuple(s.strip() for s in raw.split(",") if s.strip())
            else:
                kwargs[f.name] = raw
        cfg = cls(**kwargs)  # type: ignore[arg-type]
        if cfg.passive_mode not in ("auto", "on", "off"):
            raise ValueError("FACE_PASSIVE_LIVENESS must be auto, on or off")
        return cfg
