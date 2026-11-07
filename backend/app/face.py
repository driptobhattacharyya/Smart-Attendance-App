"""Face detection + recognition with OpenCV's open-source models.

* Detector: YuNet (MIT licence)        -> face_detection_yunet_2023mar.onnx
* Recogniser: SFace (Apache-2.0)        -> face_recognition_sface_2021dec.onnx

Both come from https://github.com/opencv/opencv_zoo and are downloaded by
``scripts/download_models.sh`` (the Dockerfile runs it at build time).

Only the 128-number embedding is stored per user, never the photo.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol

import numpy as np

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"


class FaceError(Exception):
    """A user-facing problem with the submitted photo (no face, several faces...)."""


class FaceEngine(Protocol):
    def embed(self, image_bytes: bytes) -> list[float]:
        """Return the embedding of the single face in the image or raise FaceError."""

    def similarity(self, a: list[float], b: list[float]) -> float:
        """Cosine similarity between two embeddings (higher = more alike)."""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class OpenCVFaceEngine:
    def __init__(self, models_dir: str | Path, score_threshold: float = 0.8):
        import cv2  # imported lazily so tests don't need the models

        models_dir = Path(models_dir)
        detector_path = models_dir / DETECTOR_FILE
        recognizer_path = models_dir / RECOGNIZER_FILE
        missing = [str(p) for p in (detector_path, recognizer_path) if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f"Face models missing: {', '.join(missing)}. Run scripts/download_models.sh"
            )
        self._cv2 = cv2
        self._detector = cv2.FaceDetectorYN.create(
            str(detector_path), "", (320, 320), score_threshold, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        # OpenCV DNN objects are not thread-safe; FastAPI runs sync work in a thread pool.
        self._lock = threading.Lock()

    def _decode(self, image_bytes: bytes) -> np.ndarray:
        buf = np.frombuffer(image_bytes, dtype=np.uint8)
        img = self._cv2.imdecode(buf, self._cv2.IMREAD_COLOR)
        if img is None:
            raise FaceError("Could not read the image. Please upload a JPEG or PNG photo.")
        # Phone photos can be huge; detection works best around 640px.
        h, w = img.shape[:2]
        scale = 640 / max(h, w)
        if scale < 1:
            img = self._cv2.resize(img, (int(w * scale), int(h * scale)))
        return img

    def embed(self, image_bytes: bytes) -> list[float]:
        img = self._decode(image_bytes)
        h, w = img.shape[:2]
        with self._lock:
            self._detector.setInputSize((w, h))
            _, faces = self._detector.detect(img)
            if faces is None or len(faces) == 0:
                raise FaceError("No face detected. Face the camera in good light and try again.")
            if len(faces) > 1:
                raise FaceError("More than one face detected. Make sure only you are in frame.")
            aligned = self._recognizer.alignCrop(img, faces[0])
            feature = self._recognizer.feature(aligned)
        return [float(x) for x in np.asarray(feature).flatten()]

    def similarity(self, a: list[float], b: list[float]) -> float:
        return cosine_similarity(a, b)
