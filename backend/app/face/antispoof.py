"""Passive anti-spoofing with MiniFASNet (Silent-Face-Anti-Spoofing, Apache-2.0,
https://github.com/minivision-ai/Silent-Face-Anti-Spoofing).

!!! ACCURACY UNVERIFIED !!!  This was written from knowledge of the original repo's inference
code (``src/anti_spoof_predict.py``, ``src/generate_patches.py``, ``src/utility.py``) because
the models could not be downloaded when it was written. Pre/post-processing is believed faithful
but has never been run against the real ONNX files. Before relying on it, follow the evaluation
protocol in docs/face-liveness.md and check the real-model output on known real/spoof samples.

Runtime choice: ``cv2.dnn`` (already a dependency) rather than onnxruntime, which would add
tens of MB to the Lambda image. If cv2.dnn cannot import a given export (e.g. an unsupported
op), swap ``_forward`` for an onnxruntime session; the rest is unchanged.

Original inference, per model:
  1. Face box (x, y, w, h) from a detector (RetinaFace there, YuNet here: boxes differ slightly).
  2. ``CropImage._get_new_box``: expand the box by the model's *scale* (2.7 for
     ``2.7_80x80_MiniFASNetV2``, 4.0 for ``4_0_0_80x80_MiniFASNetV1SE``), clamped to the image,
     shifted back inside the image if it overflows; crop; resize to 80x80 (BGR, cv2.resize).
  3. ``ToTensor`` from the repo's own transform: HWC->CHW float32 **without** dividing by 255,
     BGR order, no mean/std.
  4. softmax over 3 logits; class 1 = real face, classes 0 and 2 = spoof types.
  5. The repo sums the two models' softmax vectors; we average, so P(real) is in 0..1.
"""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from .config import FaceConfig
from .errors import SpoofModelMissingError
from .types import FaceObs

INPUT_SIZE = 80
# (onnx file name, crop scale). Names are our own; see scripts/download_models.sh.
MINIFASNET_MODELS: tuple[tuple[str, float], ...] = (
    ("2.7_80x80_MiniFASNetV2.onnx", 2.7),
    ("4_0_0_80x80_MiniFASNetV1SE.onnx", 4.0),
)


def new_box(
    src_w: int, src_h: int, bbox: tuple[float, float, float, float], scale: float
) -> tuple[int, int, int, int]:
    """Port of ``CropImage._get_new_box``. Returns (left, top, right, bottom), inclusive."""
    x, y, box_w, box_h = bbox
    scale = min((src_h - 1) / box_h, (src_w - 1) / box_w, scale)
    new_w, new_h = box_w * scale, box_h * scale
    cx, cy = box_w / 2 + x, box_h / 2 + y
    lt_x, lt_y = cx - new_w / 2, cy - new_h / 2
    rb_x, rb_y = cx + new_w / 2, cy + new_h / 2
    if lt_x < 0:
        rb_x -= lt_x
        lt_x = 0
    if lt_y < 0:
        rb_y -= lt_y
        lt_y = 0
    if rb_x > src_w - 1:
        lt_x -= rb_x - src_w + 1
        rb_x = src_w - 1
    if rb_y > src_h - 1:
        lt_y -= rb_y - src_h + 1
        rb_y = src_h - 1
    return int(lt_x), int(lt_y), int(rb_x), int(rb_y)


def crop_patch(
    img: np.ndarray, bbox: tuple[float, float, float, float], scale: float
) -> np.ndarray:
    h, w = img.shape[:2]
    left, t, r, b = new_box(w, h, bbox, scale)
    patch = img[t : b + 1, left : r + 1]
    return cv2.resize(patch, (INPUT_SIZE, INPUT_SIZE))


def softmax(logits: np.ndarray) -> np.ndarray:
    z = logits.astype(np.float64) - np.max(logits)
    e = np.exp(z)
    return e / e.sum()


class MiniFASNetSpoofDetector:
    """``score(img, face) -> P(real)`` in 0..1 (mean over the loaded models)."""

    def __init__(self, models_dir: str | Path, cfg: FaceConfig | None = None):
        self._cfg = cfg or FaceConfig()
        d = Path(models_dir)
        paths = [(d / name, scale) for name, scale in MINIFASNET_MODELS]
        missing = [str(p) for p, _ in paths if not p.exists()]
        if missing:
            raise SpoofModelMissingError(f"Anti-spoofing models missing: {', '.join(missing)}")
        self._nets = [(cv2.dnn.readNetFromONNX(str(p)), s) for p, s in paths]
        self._lock = threading.Lock()  # cv2.dnn nets are not thread-safe

    def _forward(self, net, patch: np.ndarray) -> np.ndarray:
        factor = 1.0 / 255.0 if self._cfg.spoof_divide_255 else 1.0
        blob = cv2.dnn.blobFromImage(
            patch, scalefactor=factor, size=(INPUT_SIZE, INPUT_SIZE), mean=(0, 0, 0), swapRB=False
        )
        net.setInput(blob)
        return np.asarray(net.forward()).reshape(-1)

    def score(self, img: np.ndarray, face: FaceObs) -> float:
        probs = []
        with self._lock:
            for net, scale in self._nets:
                patch = crop_patch(img, face.bbox, scale)
                probs.append(softmax(self._forward(net, patch)))
                patch.fill(0)
        return float(np.mean([p[1] for p in probs]))


def load_spoof_detector(cfg: FaceConfig) -> MiniFASNetSpoofDetector | None:
    """Apply FACE_PASSIVE_LIVENESS: off -> None; auto -> detector iff the files exist;
    on -> detector or raise SpoofModelMissingError (call at startup so misconfiguration is loud,
    never a silent pass)."""
    if cfg.passive_mode == "off":
        return None
    present = all((Path(cfg.models_dir) / n).exists() for n, _ in MINIFASNET_MODELS)
    if cfg.passive_mode == "auto" and not present:
        return None
    return MiniFASNetSpoofDetector(cfg.models_dir, cfg)  # raises if 'on' and missing
