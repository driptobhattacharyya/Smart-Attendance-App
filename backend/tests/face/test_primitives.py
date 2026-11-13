"""Pure utilities: challenge tokens, yaw geometry, image quality, config, anti-spoof helpers."""

import random

import numpy as np
import pytest

from app.face import FaceConfig, SpoofModelMissingError
from app.face import liveness as lv
from app.face.antispoof import crop_patch, load_spoof_detector, new_box, softmax
from app.face.challenge import new_challenge, sign_challenge, verify_challenge_token
from app.face.errors import ChallengeError
from app.face.types import FaceObs

from .fakes import landmarks_for

KEY = b"k" * 32


# ---------------------------------------------------------------- challenge


def test_challenge_roundtrip_and_determinism():
    ch = new_challenge(random.Random(1), now=1000.0)
    assert ch.action in ("turn_left", "turn_right")
    assert ch.expires_in == 60
    assert new_challenge(random.Random(1), now=1000.0) == ch
    got = verify_challenge_token(sign_challenge(ch, KEY), KEY, now=1010.0)
    assert got == ch


def test_challenge_expired():
    ch = new_challenge(random.Random(2), now=1000.0)
    tok = sign_challenge(ch, KEY)
    verify_challenge_token(tok, KEY, now=1060.0)
    with pytest.raises(ChallengeError) as e:
        verify_challenge_token(tok, KEY, now=1061.0)
    assert e.value.reason == "expired"


def test_challenge_tampered_and_wrong_key():
    ch = new_challenge(random.Random(3), actions=("turn_left",), now=1000.0)
    tok = sign_challenge(ch, KEY)
    body, sig = tok.split(".")
    # flip a character in the payload
    bad_body = ("A" if body[5] != "A" else "B").join([body[:5], body[6:]])
    for bad in (f"{bad_body}.{sig}", f"{body}.{'A' * len(sig)}"):
        with pytest.raises(ChallengeError) as e:
            verify_challenge_token(bad, KEY, now=1001.0)
        assert e.value.reason in ("bad_signature", "malformed")
    with pytest.raises(ChallengeError) as e:
        verify_challenge_token(tok, b"x" * 32, now=1001.0)
    assert e.value.reason == "bad_signature"


def test_challenge_malformed_future_and_subject():
    for junk in ("", "abc", "a.b.c", "!!.!!"):
        with pytest.raises(ChallengeError):
            verify_challenge_token(junk, KEY)
    ch = new_challenge(random.Random(4), now=5000.0, subject="u1")
    tok = sign_challenge(ch, KEY)
    with pytest.raises(ChallengeError) as e:
        verify_challenge_token(tok, KEY, subject="u1", now=1000.0)
    assert e.value.reason == "not_yet_valid"
    with pytest.raises(ChallengeError) as e:
        verify_challenge_token(tok, KEY, subject="u2", now=5001.0)
    assert e.value.reason == "wrong_subject"
    assert verify_challenge_token(tok, KEY, subject="u1", now=5001.0).subject == "u1"


def test_challenge_rejects_bad_config():
    with pytest.raises(ValueError):
        new_challenge(actions=("fly",))
    with pytest.raises(ValueError):
        sign_challenge(new_challenge(), b"")


# ---------------------------------------------------------------- yaw


def test_yaw_frontal_left_right_symmetry():
    assert lv.yaw_from_landmarks(landmarks_for(0)) == pytest.approx(0, abs=1e-6)
    left = lv.yaw_from_landmarks(landmarks_for(25))
    right = lv.yaw_from_landmarks(landmarks_for(-25))
    assert left == pytest.approx(25, abs=0.01)
    assert right == pytest.approx(-25, abs=0.01)


def test_yaw_roll_invariant_and_mirror():
    lm = landmarks_for(20)
    th = np.radians(30)
    rot = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    rolled = (lm - lm.mean(0)) @ rot.T + lm.mean(0)
    assert lv.yaw_from_landmarks(rolled) == pytest.approx(20, abs=0.01)
    assert lv.yaw_from_landmarks(lm, FaceConfig(mirrored_frames=True)) == pytest.approx(
        -20, abs=0.01
    )


def test_yaw_degenerate():
    lm = landmarks_for(0)
    lm[1] = lm[0]
    with pytest.raises(ValueError):
        lv.yaw_from_landmarks(lm)


def test_evaluate_turn_rules():
    cfg = FaceConfig()
    assert lv.evaluate_turn([0, 3, 25], "turn_left", cfg).passed
    assert not lv.evaluate_turn([0, 3, 25], "turn_right", cfg).passed
    assert lv.evaluate_turn([0, -2, -22], "turn_right", cfg).passed
    assert not lv.evaluate_turn([0, 5, 10], "turn_left", cfg).passed  # too small
    assert not lv.evaluate_turn([30, 50, 70], "turn_left", cfg).passed  # baseline not frontal


def test_evaluate_blink():
    cfg = FaceConfig()
    assert lv.evaluate_blink([1.0, 1.0, 0.0], cfg).passed
    assert not lv.evaluate_blink([1.0, 1.0, 1.0], cfg).passed
    assert not lv.evaluate_blink([1.0, None, 0.0], cfg).passed


# ---------------------------------------------------------------- image quality


def _noise(seed=0, size=200, lo=60, hi=200):
    return np.random.default_rng(seed).integers(lo, hi, (size, size, 3), dtype=np.uint8)


def test_brightness_and_blur_metrics():
    flat = np.full((100, 100), 128, np.uint8)
    assert lv.brightness(flat) == 128
    assert lv.blur_variance(flat) == 0
    sharp = _noise()[:, :, 0]
    import cv2

    blurred = cv2.GaussianBlur(sharp, (0, 0), 6)
    assert lv.blur_variance(sharp) > 100 * lv.blur_variance(blurred) > 0


def test_quality_gates():
    cfg = FaceConfig()
    face = FaceObs((40, 40, 120, 120), landmarks_for(0))
    assert lv.check_quality(_noise(), face, cfg).ok
    assert lv.check_quality(_noise(lo=0, hi=20), face, cfg).reason == "too_dark"
    assert lv.check_quality(_noise(lo=235, hi=256), face, cfg).reason == "too_bright"
    assert lv.check_quality(np.full((200, 200, 3), 128, np.uint8), face, cfg).reason == "too_blurry"
    small = FaceObs((40, 40, 50, 50), landmarks_for(0))
    assert lv.check_quality(_noise(), small, cfg).reason == "face_too_small"


def test_static_frames_and_pairwise_similarity():
    cfg = FaceConfig()
    a = lv.thumbnail(_noise(1))
    assert lv.frames_are_static([a, a.copy()], cfg)
    assert not lv.frames_are_static([a, lv.thumbnail(_noise(2))], cfg)
    v = np.ones(8, np.float32)
    assert lv.pairwise_min_similarity([v, v * 3]) == pytest.approx(1.0)
    assert lv.pairwise_min_similarity([v, -v]) == pytest.approx(-1.0)


# ---------------------------------------------------------------- config + antispoof helpers


def test_config_from_env():
    cfg = FaceConfig.from_env(
        {
            "FACE_PASSIVE_LIVENESS": "off",
            "FACE_MIN_YAW_CHANGE_DEG": "22.5",
            "FACE_CHALLENGE_ACTIONS": "turn_left, blink",
            "FACE_MIRRORED_FRAMES": "true",
        }
    )
    assert cfg.passive_mode == "off"
    assert cfg.min_yaw_change_deg == 22.5
    assert cfg.challenge_actions == ("turn_left", "blink")
    assert cfg.mirrored_frames is True
    with pytest.raises(ValueError):
        FaceConfig.from_env({"FACE_PASSIVE_LIVENESS": "maybe"})


def test_passive_mode_loading(tmp_path):
    assert load_spoof_detector(FaceConfig(passive_mode="off", models_dir=str(tmp_path))) is None
    assert load_spoof_detector(FaceConfig(passive_mode="auto", models_dir=str(tmp_path))) is None
    with pytest.raises(SpoofModelMissingError):  # never a silent pass when configured on
        load_spoof_detector(FaceConfig(passive_mode="on", models_dir=str(tmp_path)))


def test_minifasnet_crop_geometry_and_softmax():
    box = new_box(640, 480, (300, 200, 100, 120), 2.7)
    left, top, right, bottom = box
    assert 0 <= left < right <= 639 and 0 <= top < bottom <= 479
    # near the edge the crop is shifted back inside, not clipped to a smaller one
    edge = new_box(640, 480, (0, 0, 100, 100), 2.7)
    assert edge[0] == 0 and edge[1] == 0 and edge[2] - edge[0] >= 260
    patch = crop_patch(_noise(size=300), (100, 100, 80, 80), 4.0)
    assert patch.shape == (80, 80, 3)
    p = softmax(np.array([1.0, 3.0, 0.5]))
    assert p.sum() == pytest.approx(1.0) and p.argmax() == 1
