"""Face providers with multi-frame verification + liveness.

Both providers are synchronous and self-contained (no DB, no FastAPI). Async callers wrap them
with ``asyncio.to_thread``. They never log or persist frames. Memory hygiene is best effort:
decoded numpy images are zeroed after use and ``bytearray`` inputs are zeroed in place, but
Python ``bytes`` are immutable and the caller's request buffer cannot be wiped from here.

Frame order contract: ``frames[0]`` is the frontal baseline, later frames are captured while
the user performs the challenge.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol

import numpy as np

from . import liveness as lv
from .antispoof import load_spoof_detector
from .config import FaceConfig
from .engine import FaceEngine, OpenCVEngine
from .errors import FaceError, FaceUnavailableError
from .types import Challenge, EnrolledFace, FaceObs, VerifyResult

Frame = bytes | bytearray | memoryview


class SpoofDetector(Protocol):
    def score(self, img: np.ndarray, face: FaceObs) -> float:
        """P(real face) in 0..1."""


class FaceProvider(Protocol):
    name: str

    def enroll(self, frames: list[Frame]) -> EnrolledFace: ...

    def verify(
        self, enrolled: EnrolledFace, frames: list[Frame], challenge: Challenge
    ) -> VerifyResult: ...


def _wipe(frames: list[Frame]) -> None:
    for f in frames:
        if isinstance(f, bytearray):
            f[:] = bytes(len(f))


def l2_normalise(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(l2_normalise(a.ravel()), l2_normalise(b.ravel())))


class _Reject(Exception):
    def __init__(self, reason: str, detail: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


@dataclass
class _Analysed:
    img: np.ndarray
    face: FaceObs
    quality: lv.QualityReport


# ============================================================================= OpenCV


class OpenCVProvider:
    """YuNet + SFace identity, landmark active challenge, optional MiniFASNet passive check."""

    name = "opencv"

    def __init__(
        self,
        engine: FaceEngine,
        spoof: SpoofDetector | None = None,
        cfg: FaceConfig | None = None,
    ):
        self._engine = engine
        self._spoof = spoof
        self.cfg = cfg or FaceConfig()

    @property
    def passive_enabled(self) -> bool:
        return self._spoof is not None

    # ---- frame analysis

    def _analyse(self, data: Frame) -> _Analysed:
        if len(data) > self.cfg.max_frame_bytes:
            raise _Reject("frame_too_large")
        try:
            img = self._engine.decode(bytes(data))
        except FaceError as exc:
            raise _Reject("bad_image") from exc
        try:
            faces = self._engine.detect(img)
            if not faces:
                raise _Reject("no_face")
            if len(faces) > 1:
                raise _Reject("multiple_faces", {"count": len(faces)})
            q = lv.check_quality(img, faces[0], self.cfg)
            if not q.ok:
                raise _Reject(q.reason, q.metrics)
            return _Analysed(img, faces[0], q)
        except BaseException:
            img.fill(0)
            raise

    def _analyse_all(self, frames: list[Frame]) -> list[_Analysed]:
        out: list[_Analysed] = []
        try:
            for i, f in enumerate(frames):
                try:
                    out.append(self._analyse(f))
                except _Reject as r:
                    r.detail = {**r.detail, "frame": i}
                    raise
        except BaseException:
            for a in out:
                a.img.fill(0)
            raise
        return out

    # ---- enrol

    def enroll(self, frames: list[Frame]) -> EnrolledFace:
        cfg = self.cfg
        try:
            if not cfg.enroll_min_frames <= len(frames) <= cfg.enroll_max_frames:
                raise FaceError(
                    f"Send between {cfg.enroll_min_frames} and {cfg.enroll_max_frames} photos."
                )
            try:
                analysed = self._analyse_all(frames)
            except _Reject as r:
                raise FaceError(_ENROLL_MESSAGES.get(r.reason, r.reason)) from r
            try:
                embs = [self._engine.embed(a.img, a.face) for a in analysed]
                if len(embs) > 1 and lv.pairwise_min_similarity(embs) < cfg.same_person_threshold:
                    raise FaceError(
                        "The photos do not appear to show the same person. Please retake them."
                    )
                if self._spoof is not None:
                    res = lv.evaluate_passive(
                        [self._spoof.score(a.img, a.face) for a in analysed], cfg
                    )
                    if not res.passed:
                        raise FaceError(
                            "The photo looks like a picture of a screen or print. "
                            "Please take a live photo."
                        )
            finally:
                for a in analysed:
                    a.img.fill(0)
            mean = l2_normalise(np.mean([l2_normalise(e) for e in embs], axis=0))
            return EnrolledFace(
                provider=self.name,
                embedding=[float(x) for x in mean],
                model=self._engine.model_id,
                frames_used=len(embs),
            )
        finally:
            _wipe(frames)

    # ---- verify

    def verify(
        self,
        enrolled: EnrolledFace,
        frames: list[Frame],
        challenge: Challenge,
        *,
        now: float | None = None,
    ) -> VerifyResult:
        checks: dict[str, Any] = {"action": challenge.action}
        score = 0.0
        liveness = 0.0
        analysed: list[_Analysed] = []

        def fail(reason: str) -> VerifyResult:
            return VerifyResult(False, score, liveness, checks, reason)

        try:
            cfg = self.cfg
            if challenge.is_expired(now):
                checks["challenge"] = "expired"
                return fail("challenge_expired")
            if enrolled.embedding is None or enrolled.model != self._engine.model_id:
                checks["enrolment"] = "missing_or_model_changed"
                return fail("not_enrolled")
            if not cfg.min_frames <= len(frames) <= cfg.max_frames:
                checks["frames"] = {
                    "got": len(frames),
                    "min": cfg.min_frames,
                    "max": cfg.max_frames,
                }
                return fail("bad_frame_count")

            # 1. decode + single face + quality on every frame
            try:
                analysed = self._analyse_all(frames)
            except _Reject as r:
                checks["quality"] = {"ok": False, "reason": r.reason, **r.detail}
                return fail(r.reason)
            checks["quality"] = {"ok": True, "frames": [a.quality.metrics for a in analysed]}

            # 2. replayed still
            thumbs = [lv.thumbnail(a.img) for a in analysed]
            static = lv.frames_are_static(thumbs, cfg)
            checks["static_frames"] = static
            if static:
                return fail("static_frames")

            # 3. same person across frames
            embs = [self._engine.embed(a.img, a.face) for a in analysed]
            lowest = lv.pairwise_min_similarity(embs)
            checks["same_person"] = {"min_similarity": round(lowest, 3), "ok": True}
            if lowest < cfg.same_person_threshold:
                checks["same_person"]["ok"] = False
                return fail("identity_changed")

            # 4. matches the enrolled face
            ref = np.asarray(enrolled.embedding, dtype=np.float32)
            sims = [cosine(ref, e) for e in embs]
            score = float(np.mean(sims))
            checks["match"] = {
                "scores": [round(s, 3) for s in sims],
                "ok": score >= cfg.match_threshold,
            }
            if score < cfg.match_threshold:
                return fail("face_mismatch")

            # 5. active challenge
            yaws = [lv.yaw_from_landmarks(a.face.landmarks, cfg) for a in analysed]
            openness = (
                [self._engine.eye_openness(a.img, a.face) for a in analysed]
                if challenge.action == "blink"
                else []
            )
            active = lv.evaluate_action(challenge.action, yaws, openness, cfg)
            checks["active"] = {"passed": active.passed, **active.detail}
            liveness = active.strength
            if not active.passed:
                return fail("challenge_failed")

            # 6. passive anti-spoofing
            if self._spoof is not None:
                passive = lv.evaluate_passive(
                    [self._spoof.score(a.img, a.face) for a in analysed], cfg
                )
                checks["passive"] = {"passed": passive.passed, **passive.detail}
                liveness = passive.score
                if not passive.passed:
                    return fail("spoof_detected")
            else:
                checks["passive"] = "disabled"

            return VerifyResult(True, score, liveness, checks, "ok")
        finally:
            for a in analysed:
                a.img.fill(0)
            _wipe(frames)


_ENROLL_MESSAGES = {
    "bad_image": "Could not read the image. Please send a JPEG or PNG photo.",
    "frame_too_large": "The photo is too large.",
    "no_face": "No face detected. Face the camera in good light and try again.",
    "multiple_faces": "More than one face detected. Make sure only you are in frame.",
    "face_too_small": "Your face is too small in the photo. Move closer.",
    "too_dark": "The photo is too dark. Find better light.",
    "too_bright": "The photo is too bright. Avoid direct light on the camera.",
    "too_blurry": "The photo is blurry. Hold the phone steady.",
}


def build_opencv_provider(cfg: FaceConfig | None = None) -> OpenCVProvider:
    """Startup helper. Raises SpoofModelMissingError if FACE_PASSIVE_LIVENESS=on and the
    anti-spoofing models are absent; silently runs without passive check only in ``auto``."""
    cfg = cfg or FaceConfig.from_env()
    engine = OpenCVEngine(cfg.models_dir, cfg)
    return OpenCVProvider(engine, load_spoof_detector(cfg), cfg)


# ============================================================================= Azure


class AzureProvider:
    """Azure AI Face: identity via ``verify_from_large_person_group`` per frame; the challenge
    uses Azure's head-pose attribute. There is NO passive anti-spoofing here (Azure's liveness
    is a separate session-based service that needs the app SDK); ``checks["passive"]`` says so.
    Blink is unsupported and always fails. Limited Access approval is required by Microsoft.

    Only the Azure person id is stored; the person is named with a random uuid (no PII sent).
    """

    name = "azure"
    DETECTION_MODEL = "detection_03"
    RECOGNITION_MODEL = "recognition_04"
    # Sign convention of Azure's headPose.yaw vs ours (positive = subject's left) is UNVERIFIED.
    YAW_SIGN = 1.0

    def __init__(
        self,
        endpoint: str = "",
        key: str = "",
        group_id: str = "attendify",
        *,
        face_client: Any = None,
        admin_client: Any = None,
        cfg: FaceConfig | None = None,
    ):
        if face_client is None or admin_client is None:
            try:
                from azure.ai.vision.face import FaceAdministrationClient, FaceClient
                from azure.core.credentials import AzureKeyCredential
            except ImportError as exc:  # pragma: no cover - optional extra
                raise RuntimeError("Azure provider needs: uv sync --extra azure") from exc
            if not endpoint or not key:
                raise RuntimeError("Set AZURE_FACE_ENDPOINT and AZURE_FACE_KEY")
            cred = AzureKeyCredential(key)
            face_client = face_client or FaceClient(endpoint, cred)
            admin_client = admin_client or FaceAdministrationClient(endpoint, cred)
        self._face = face_client
        self._groups = admin_client.large_person_group
        self._group_id = group_id
        self._group_ready = False
        self._lock = threading.Lock()
        self.cfg = cfg or FaceConfig()

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

    def _detect_one(self, image: bytes, *, attributes: bool = False) -> Any:
        kwargs: dict[str, Any] = {
            "detection_model": self.DETECTION_MODEL,
            "recognition_model": self.RECOGNITION_MODEL,
            "return_face_id": True,
        }
        if attributes:
            kwargs["return_face_attributes"] = ["headPose"]
        try:
            faces = self._face.detect(image, **kwargs)
        except Exception as exc:
            _reraise(exc)
        if not faces:
            raise _Reject("no_face")
        if len(faces) > 1:
            raise _Reject("multiple_faces")
        return faces[0]

    def enroll(self, frames: list[Frame]) -> EnrolledFace:
        cfg = self.cfg
        try:
            if not cfg.enroll_min_frames <= len(frames) <= cfg.enroll_max_frames:
                raise FaceError(
                    f"Send between {cfg.enroll_min_frames} and {cfg.enroll_max_frames} photos."
                )
            images = [bytes(f) for f in frames]
            try:
                for im in images:
                    self._detect_one(im)
            except _Reject as r:
                raise FaceError(_ENROLL_MESSAGES.get(r.reason, r.reason)) from r
            try:
                self._ensure_group()
                person = self._groups.create_person(self._group_id, name=uuid.uuid4().hex)
                try:
                    for im in images:
                        self._groups.add_face(
                            self._group_id,
                            person.person_id,
                            im,
                            detection_model=self.DETECTION_MODEL,
                        )
                    self._groups.begin_train(self._group_id).result()
                except Exception:
                    self._groups.delete_person(self._group_id, person.person_id)
                    raise
            except Exception as exc:
                _reraise(exc)
            return EnrolledFace(
                provider=self.name,
                external_id=person.person_id,
                model=self.RECOGNITION_MODEL,
                frames_used=len(images),
            )
        finally:
            _wipe(frames)

    def reset(self, enrolled: EnrolledFace) -> None:
        """Delete the person on Azure (call when an employee leaves)."""
        if not enrolled.external_id:
            return
        try:
            self._groups.delete_person(self._group_id, enrolled.external_id)
        except Exception as exc:
            if _status(exc) != 404:
                _reraise(exc)

    def verify(
        self,
        enrolled: EnrolledFace,
        frames: list[Frame],
        challenge: Challenge,
        *,
        now: float | None = None,
    ) -> VerifyResult:
        cfg = self.cfg
        checks: dict[str, Any] = {"action": challenge.action, "passive": "unavailable_on_azure"}
        score = 0.0

        def fail(reason: str, liveness: float = 0.0) -> VerifyResult:
            return VerifyResult(False, score, liveness, checks, reason)

        try:
            if challenge.is_expired(now):
                return fail("challenge_expired")
            if not enrolled.external_id:
                return fail("not_enrolled")
            if not cfg.min_frames <= len(frames) <= cfg.max_frames:
                return fail("bad_frame_count")
            if challenge.action == "blink":
                checks["active"] = {"error": "blink_unsupported_on_azure"}
                return fail("challenge_failed")
            try:
                faces = [self._detect_one(bytes(f), attributes=True) for f in frames]
            except _Reject as r:
                checks["quality"] = {"ok": False, "reason": r.reason}
                return fail(r.reason)
            confidences = []
            for face in faces:
                try:
                    res = self._face.verify_from_large_person_group(
                        face_id=face.face_id,
                        large_person_group_id=self._group_id,
                        person_id=enrolled.external_id,
                    )
                except Exception as exc:
                    _reraise(exc)
                if not res.is_identical:
                    score = float(res.confidence)
                    checks["match"] = {"ok": False}
                    return fail("face_mismatch")
                confidences.append(float(res.confidence))
            score = float(np.mean(confidences))
            # every frame matches the same enrolled person -> same person across frames
            checks["match"] = {"ok": True, "scores": [round(c, 3) for c in confidences]}
            try:
                yaws = [self.YAW_SIGN * float(f.face_attributes.head_pose.yaw) for f in faces]
            except AttributeError:
                checks["active"] = {"error": "head_pose_unavailable"}
                return fail("challenge_failed")
            active = lv.evaluate_turn(yaws, challenge.action, cfg)
            checks["active"] = {"passed": active.passed, **active.detail}
            if not active.passed:
                return fail("challenge_failed", active.strength)
            return VerifyResult(True, score, active.strength, checks, "ok")
        finally:
            _wipe(frames)


def _status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def _reraise(exc: Exception) -> NoReturn:
    if isinstance(exc, FaceError | FaceUnavailableError):
        raise exc
    if _status(exc) == 400:
        raise FaceError(
            "The photo could not be used. Please try again with a clear photo."
        ) from exc
    raise FaceUnavailableError(f"Face service error: {exc}") from exc
