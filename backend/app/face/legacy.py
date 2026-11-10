"""LEGACY single-image providers (the original app/face.py), kept for the existing app.
New code should use app.face.providers. Original docstring follows.

Face providers: pluggable face enrolment + verification.

``FACE_PROVIDER=opencv`` (default) uses OpenCV's open-source models:

* Detector: YuNet (MIT licence)        -> face_detection_yunet_2023mar.onnx
* Recogniser: SFace (Apache-2.0)        -> face_recognition_sface_2021dec.onnx

Both come from https://github.com/opencv/opencv_zoo and are downloaded by
``scripts/download_models.sh`` (the Dockerfile runs it at build time).

Only the 128-number embedding is stored per user, never the photo.

``FACE_PROVIDER=azure`` uses Azure AI Face (see ``AzureFaceProvider``).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, NoReturn, Protocol

import numpy as np

from .errors import FaceError, FaceUnavailableError

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"


class FaceProvider(Protocol):
    """What the API needs from a face backend."""

    name: str

    def is_enrolled(self, user: dict) -> bool: ...

    async def enroll(self, user: dict, image: bytes) -> dict:
        """Register the user's face. Returns the fields to ``$set`` on the user document."""

    async def verify(self, user: dict, image: bytes) -> tuple[bool, float]:
        """Compare a live photo with the enrolled face -> (verified, score)."""

    async def reset(self, user: dict) -> list[str]:
        """Forget the user's face. Returns the user-document fields to ``$unset``."""


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


class OpenCVFaceProvider:
    """Stores the SFace embedding on the user (``face_embedding``); compares locally."""

    name = "opencv"

    def __init__(self, engine: FaceEngine, threshold: float):
        self._engine = engine
        self._threshold = threshold

    def is_enrolled(self, user: dict) -> bool:
        return bool(user.get("face_embedding"))

    async def enroll(self, user: dict, image: bytes) -> dict:
        embedding = await asyncio.to_thread(self._engine.embed, image)
        return {"face_embedding": embedding}

    async def verify(self, user: dict, image: bytes) -> tuple[bool, float]:
        embedding = await asyncio.to_thread(self._engine.embed, image)
        score = self._engine.similarity(user["face_embedding"], embedding)
        return score >= self._threshold, score

    async def reset(self, user: dict) -> list[str]:
        return ["face_embedding"]


class AzureFaceProvider:
    """Azure AI Face (``azure-ai-vision-face``): one LargePersonGroup, one person per user.

    Enrolment: create_person + add_face + train. Verification: detect the live photo, then
    ``verify_from_large_person_group`` against the stored ``azure_person_id``. Only the Azure
    person id is kept on our side.

    NOTE: Microsoft gates face identification/verification behind Limited Access approval.
    Apply at https://aka.ms/facerecognition and use a resource that has been approved,
    otherwise these calls fail with an access-denied error (surfaced here as a 503).

    The SDK is synchronous, so every call runs in a worker thread. The SDK is imported lazily
    (install with ``uv sync --extra azure``); tests inject mock clients.
    """

    name = "azure"
    # detection_03 + recognition_04 are Azure's most accurate models.
    DETECTION_MODEL = "detection_03"
    RECOGNITION_MODEL = "recognition_04"

    def __init__(
        self,
        endpoint: str = "",
        key: str = "",
        group_id: str = "attendify",
        *,
        face_client: Any = None,
        admin_client: Any = None,
    ):
        if face_client is None or admin_client is None:
            try:
                from azure.ai.vision.face import FaceAdministrationClient, FaceClient
                from azure.core.credentials import AzureKeyCredential
            except ImportError as exc:  # pragma: no cover - depends on the optional extra
                raise RuntimeError(
                    "FACE_PROVIDER=azure needs the optional SDK: uv sync --extra azure"
                ) from exc
            if not endpoint or not key:
                raise RuntimeError(
                    "Set AZURE_FACE_ENDPOINT and AZURE_FACE_KEY for FACE_PROVIDER=azure"
                )
            credential = AzureKeyCredential(key)
            face_client = face_client or FaceClient(endpoint, credential)
            admin_client = admin_client or FaceAdministrationClient(endpoint, credential)
        self._face = face_client
        self._groups = admin_client.large_person_group
        self._group_id = group_id
        self._group_ready = False
        self._lock = threading.Lock()

    def is_enrolled(self, user: dict) -> bool:
        return bool(user.get("azure_person_id"))

    # ----- blocking helpers (run in a thread) -----

    def _ensure_group(self) -> None:
        with self._lock:
            if self._group_ready:
                return
            try:
                self._groups.get(self._group_id)
            except Exception as exc:
                if _status(exc) != 404:
                    raise
                self._groups.create(
                    self._group_id,
                    name="Attendify employees",
                    recognition_model=self.RECOGNITION_MODEL,
                )
            self._group_ready = True

    def _detect_one(self, image: bytes) -> Any:
        try:
            faces = self._face.detect(
                image,
                detection_model=self.DETECTION_MODEL,
                recognition_model=self.RECOGNITION_MODEL,
                return_face_id=True,
            )
        except Exception as exc:
            _reraise(exc)
        if not faces:
            raise FaceError("No face detected. Face the camera in good light and try again.")
        if len(faces) > 1:
            raise FaceError("More than one face detected. Make sure only you are in frame.")
        return faces[0]

    def _enroll(self, user: dict, image: bytes) -> dict:
        self._detect_one(image)  # reject no-face / several faces before creating anything
        try:
            self._ensure_group()
            person = self._groups.create_person(
                self._group_id, name=user["employee_id"], user_data=user["employee_id"]
            )
            try:
                self._groups.add_face(
                    self._group_id,
                    person.person_id,
                    image,
                    detection_model=self.DETECTION_MODEL,
                )
                self._groups.begin_train(self._group_id).result()
            except Exception:
                self._groups.delete_person(self._group_id, person.person_id)
                raise
        except Exception as exc:
            _reraise(exc)
        return {"azure_person_id": person.person_id}

    def _verify(self, user: dict, image: bytes) -> tuple[bool, float]:
        face = self._detect_one(image)
        try:
            result = self._face.verify_from_large_person_group(
                face_id=face.face_id,
                large_person_group_id=self._group_id,
                person_id=user["azure_person_id"],
            )
        except Exception as exc:
            _reraise(exc)
        return bool(result.is_identical), float(result.confidence)

    def _reset(self, user: dict) -> None:
        try:
            self._groups.delete_person(self._group_id, user["azure_person_id"])
        except Exception as exc:
            if _status(exc) != 404:  # already gone is fine
                _reraise(exc)

    # ----- FaceProvider API -----

    async def enroll(self, user: dict, image: bytes) -> dict:
        return await asyncio.to_thread(self._enroll, user, image)

    async def verify(self, user: dict, image: bytes) -> tuple[bool, float]:
        return await asyncio.to_thread(self._verify, user, image)

    async def reset(self, user: dict) -> list[str]:
        if user.get("azure_person_id"):
            await asyncio.to_thread(self._reset, user)
        return ["azure_person_id"]


def _status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def _translate(exc: Exception) -> Exception:
    """Map Azure SDK errors onto our own: bad photo -> FaceError, anything else -> unavailable."""
    if isinstance(exc, FaceError | FaceUnavailableError):
        return exc
    if _status(exc) == 400:
        return FaceError("The photo could not be used. Please try again with a clear photo.")
    return FaceUnavailableError(f"Face service error: {exc}")


def _reraise(exc: Exception) -> NoReturn:
    translated = _translate(exc)
    if translated is exc:
        raise exc
    raise translated from exc
