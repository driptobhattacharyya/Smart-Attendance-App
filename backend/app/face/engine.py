"""Pluggable per-frame engine: decode, detect, embed, (optionally) eye openness.

``FaceEngine`` is the seam tests fake. ``OpenCVEngine`` is YuNet (detector, MIT) + SFace
(recogniser, Apache-2.0) from opencv_zoo.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .config import FaceConfig
from .errors import FaceError
from .types import FaceObs

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"
MODEL_ID = "sface_2021dec"


class FaceEngine(Protocol):
    model_id: str

    def decode(self, data: bytes) -> np.ndarray:
        """Bytes -> BGR image (already downscaled). Raise FaceError if unreadable."""

    def detect(self, img: np.ndarray) -> list[FaceObs]: ...

    def embed(self, img: np.ndarray, face: FaceObs) -> np.ndarray:
        """Return a 1-D embedding (any scale; callers normalise)."""

    def eye_openness(self, img: np.ndarray, face: FaceObs) -> float | None:
        """0..1 (fraction of eyes judged open) or None if the engine cannot measure it."""


class OpenCVEngine:
    model_id = MODEL_ID

    def __init__(self, models_dir: str | Path, cfg: FaceConfig | None = None):
        self._cfg = cfg or FaceConfig()
        d = Path(models_dir)
        det, rec = d / DETECTOR_FILE, d / RECOGNIZER_FILE
        missing = [str(p) for p in (det, rec) if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f"Face models missing: {', '.join(missing)}. Run scripts/download_models.sh"
            )
        self._detector = cv2.FaceDetectorYN.create(
            str(det), "", (320, 320), self._cfg.detector_score_threshold, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(rec), "")
        self._eye = None
        if self._cfg.eye_cascade_path:
            cascade = cv2.CascadeClassifier(self._cfg.eye_cascade_path)
            if cascade.empty():
                raise FileNotFoundError(f"Cannot load eye cascade {self._cfg.eye_cascade_path}")
            self._eye = cascade
        self._lock = threading.Lock()  # OpenCV DNN / cascade objects are not thread-safe

    def decode(self, data: bytes) -> np.ndarray:
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise FaceError("Could not read the image. Please send a JPEG or PNG frame.")
        h, w = img.shape[:2]
        scale = self._cfg.max_image_side / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        return img

    def _raw_faces(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        self._detector.setInputSize((w, h))
        _, faces = self._detector.detect(img)
        return faces if faces is not None else np.empty((0, 15), dtype=np.float32)

    def detect(self, img: np.ndarray) -> list[FaceObs]:
        with self._lock:
            rows = self._raw_faces(img)
        out = []
        for r in rows:
            lm = np.asarray(r[4:14], dtype=np.float64).reshape(5, 2)
            out.append(
                FaceObs((float(r[0]), float(r[1]), float(r[2]), float(r[3])), lm, float(r[14]))
            )
        return out

    def embed(self, img: np.ndarray, face: FaceObs) -> np.ndarray:
        # alignCrop wants the original 15-number YuNet row: bbox, 10 landmark coords, score.
        row = np.concatenate(
            [np.asarray(face.bbox), np.asarray(face.landmarks).ravel(), [face.score]]
        ).astype(np.float32)
        with self._lock:
            aligned = self._recognizer.alignCrop(img, row)
            feat = self._recognizer.feature(aligned)
        aligned.fill(0)
        return np.asarray(feat, dtype=np.float32).ravel()

    def eye_openness(self, img: np.ndarray, face: FaceObs) -> float | None:
        if self._eye is None:
            return None
        lm = np.asarray(face.landmarks, dtype=np.float64)
        dist = float(np.linalg.norm(lm[1] - lm[0]))
        half = max(4, int(0.4 * dist))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        found = 0
        with self._lock:
            for cx, cy in lm[:2]:
                x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
                roi = cv2.equalizeHist(gray[y0 : int(cy) + half, x0 : int(cx) + half])
                if roi.size and len(
                    self._eye.detectMultiScale(roi, 1.1, 3, minSize=(half // 2,) * 2)
                ):
                    found += 1
        return found / 2.0
