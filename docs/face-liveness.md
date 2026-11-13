# Face verification and liveness

Package: `backend/app/face/` (no DB or FastAPI dependency). Frames are never stored or logged;
only embeddings and scores are kept.

## Flow

1. `GET /face/challenge`: `ch = new_challenge(subject=user_id)`, return `sign_challenge(ch, key)`.
2. App captures 3 frames: `frames[0]` frontal, later frames while performing the action.
3. `POST /face/verify`: `ch = verify_challenge_token(token, key, subject=user_id)`, then
   **record `ch.id` as used (caller's job, tokens are stateless)**, then
   `provider.verify(enrolled, frames, ch)` (sync; wrap in `asyncio.to_thread`).

## Checks (in order, first failure wins; `VerifyResult.reason`)

| Check | reason | Method |
|---|---|---|
| challenge age | `challenge_expired` | `issued_at + expires_in` |
| frame count/size | `bad_frame_count`, `frame_too_large` | 2-6 frames, 4 MB each |
| quality | `no_face`, `multiple_faces`, `face_too_small`, `too_dark`, `too_bright`, `too_blurry` | YuNet; face >= 80 px, mean gray 45-220, Laplacian variance >= 40 on a 112x112 crop |
| replayed still | `static_frames` | mean abs pixel diff < 0.5 on 64x64 thumbnails |
| same person | `identity_changed` | min pairwise SFace cosine >= 0.5 |
| matches enrolment | `face_mismatch` | mean cosine to enrolled embedding >= 0.363 |
| active | `challenge_failed` | yaw from landmarks, see below |
| passive | `spoof_detected` | MiniFASNet P(real): mean >= 0.70, every frame >= 0.40 |

`liveness_score` is the passive mean when the model ran, else the active-check strength (0-1).
`checks["passive"] == "disabled"` records when no model was loaded.

## Active check

Yaw is estimated from YuNet's 5 landmarks: offset of the nose from the eye midpoint along the
eye axis, divided by eye distance, `asin(offset / 0.5)`. Roll-invariant. Positive = subject turns
to *their* left (unmirrored frame; set `mirrored_frames` if the app mirrors). `turn_left/right`
passes if frame 0 is within 15 deg of frontal and some later frame changes by > 15 deg in the
requested direction. This is an approximation good to a few degrees; only the *change* is used.

**Blink is off by default.** YuNet gives eye centres only (no eye aspect ratio). The Haar eye
cascade is *not* shipped in opencv-python wheels (`cv2.data` is empty, checked on 5.0.0), so
openness needs `FACE_EYE_CASCADE_PATH` pointing at `haarcascade_eye.xml`. Even then a blink lasts
~150 ms, so 3 frames rarely catch one, and Haar misses on closed/small/glasses eyes can look like
blinks. The logic exists (`evaluate_blink`, tested with fakes) but enable it only with a burst of
>= 8 frames and after measuring.

## Passive anti-spoofing (MiniFASNet)

`antispoof.py`: Silent-Face-Anti-Spoofing (Apache-2.0) `MiniFASNetV2` (crop scale 2.7) +
`MiniFASNetV1SE` (scale 4.0), 80x80 BGR crops, raw 0-255 input (no /255, no mean/std), softmax
over 3 classes, class 1 = real, averaged over both models. Run through `cv2.dnn` (already a
dependency) instead of onnxruntime to keep the Lambda image small.

`FACE_PASSIVE_LIVENESS=auto|on|off`: `auto` enables iff both model files exist in
`FACE_MODELS_DIR`; `on` raises `SpoofModelMissingError` at startup if missing (call
`build_opencv_provider()` at boot); `off` disables.

**Unverified:** the models were not available when this was written. Preprocessing and
postprocessing are from memory of the original repo; cv2.dnn compatibility of the ONNX export, the
exact file names/URLs/sha256 in `scripts/download_models.sh`, the /255 question
(`FACE_SPOOF_DIVIDE_255`) and the effect of YuNet boxes vs the original RetinaFace boxes are all
untested. Treat the passive check as unproven until you run the evaluation below.

## Thresholds and where to change them

All in `FaceConfig` (`config.py`), overridable via `FACE_<FIELD>` env vars (e.g.
`FACE_MIN_YAW_CHANGE_DEG=20`). Defaults are untuned starting points.

## Known limitations

- An attacker with a video of the victim, a replay on a screen, or a 3D mask is only stopped by
  the passive model (unproven here). Active turn alone defeats static photos, not video.
- A photo rotated in front of the camera produces some apparent yaw change.
- Azure provider: identity + head-pose challenge only; no passive check, no blink.
  Azure's `headPose.yaw` sign convention is unverified (`AzureProvider.YAW_SIGN`).
- Python `bytes` are immutable, so request buffers cannot be zeroed from this code; decoded
  images and `bytearray` inputs are zeroed (best effort). No frame is written anywhere.
- Single use of a challenge must be enforced by the API layer.
- Embeddings are tied to `EnrolledFace.model`; a model change forces re-enrolment (`not_enrolled`).
- SFace accuracy degrades on very dark skin tones/low light; measure per population.

## Evaluating accuracy on real devices

1. Collect on the actual phones used: genuine attempts (>= 200, varied light, glasses, distance,
   both directions) and spoof attempts (printed photo, photo on phone/tablet screen, replayed video
   of the person, at least two devices/screens, >= 100 each).
2. Run each attempt through `verify` with `passive` forced on and log `checks` (never the frames,
   or keep them in an access-controlled study bucket with consent and delete afterwards).
3. Compute **APCER** (spoof attempts accepted / spoof attempts), **BPCER** (genuine rejected /
   genuine), and **ACER** = (APCER + BPCER) / 2, per attack type. Sweep `passive_threshold`,
   `min_yaw_change_deg`, `same_person_threshold`, `match_threshold` and pick the operating point
   (typically BPCER <= 5% with the lowest APCER; report FAR/FRR for identity separately).
4. Re-run after any model or threshold change and after phone OS/camera updates.
