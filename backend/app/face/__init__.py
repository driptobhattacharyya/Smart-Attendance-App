"""Server-side face verification + liveness, self-contained (no DB / FastAPI dependency).

New API (see docs/face-liveness.md): ``providers``, ``challenge``, ``liveness``, ``antispoof``,
``engine``, ``config``, ``types``.

Backward-compatible shim: the original single-image module ``app/face.py`` is now
``app/face/legacy.py`` (a module and a package cannot share the name ``app.face``) and its public
names are re-exported below so ``from app.face import OpenCVFaceProvider, FaceError, ...`` keeps
working until the app is replaced. The new multi-frame providers are ``OpenCVProvider`` and
``AzureProvider``.
"""

from .challenge import new_challenge, sign_challenge, verify_challenge_token
from .config import FaceConfig
from .errors import (
    ChallengeError,
    FaceError,
    FaceUnavailableError,
    SpoofModelMissingError,
)
from .legacy import (
    AzureFaceProvider,
    FaceEngine,
    FaceProvider,
    OpenCVFaceEngine,
    OpenCVFaceProvider,
    cosine_similarity,
)
from .providers import AzureProvider, OpenCVProvider, build_opencv_provider
from .types import Challenge, EnrolledFace, VerifyResult

__all__ = [
    "AzureFaceProvider",
    "AzureProvider",
    "Challenge",
    "ChallengeError",
    "EnrolledFace",
    "FaceConfig",
    "FaceEngine",
    "FaceError",
    "FaceProvider",
    "FaceUnavailableError",
    "OpenCVFaceEngine",
    "OpenCVFaceProvider",
    "OpenCVProvider",
    "SpoofModelMissingError",
    "VerifyResult",
    "build_opencv_provider",
    "cosine_similarity",
    "new_challenge",
    "sign_challenge",
    "verify_challenge_token",
]
