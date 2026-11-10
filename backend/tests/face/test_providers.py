"""Provider behaviour with a fake engine / spoof detector and mocked Azure clients."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.face import AzureProvider, EnrolledFace, FaceConfig, FaceError, FaceUnavailableError
from app.face.providers import OpenCVProvider
from app.face.types import Challenge

from .fakes import FakeEngine, FakeSpoof, frame


def challenge(action="turn_left", **kw):
    return Challenge(id="c1", action=action, issued_at=time.time(), expires_in=60, **kw)


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def provider(engine):
    return OpenCVProvider(engine, FakeSpoof(engine), FaceConfig())


@pytest.fixture
def alice(provider):
    return provider.enroll([frame("alice", seed=1), frame("alice", seed=2), frame("alice", seed=3)])


def good_frames(who="alice", direction=1, spoof=0.95):
    return [
        frame(who, 0, seed=10, spoof=spoof),
        frame(who, 8 * direction, seed=11, spoof=spoof),
        frame(who, 28 * direction, seed=12, spoof=spoof),
    ]


# ---------------------------------------------------------------- enrolment


def test_enroll_averages_and_normalises(alice):
    import numpy as np

    assert alice.provider == "opencv" and alice.frames_used == 3 and alice.model == "fake"
    assert np.linalg.norm(alice.embedding) == pytest.approx(1.0, abs=1e-5)
    assert EnrolledFace.from_dict(alice.to_dict()) == alice


def test_enroll_frame_count_bounds(provider):
    with pytest.raises(FaceError):
        provider.enroll([])
    with pytest.raises(FaceError):
        provider.enroll([frame("alice", seed=i) for i in range(4)])
    assert provider.enroll([frame("alice", seed=1)]).frames_used == 1


def test_enroll_rejects_mixed_identities(provider):
    with pytest.raises(FaceError, match="same person"):
        provider.enroll([frame("alice", seed=1), frame("bob", seed=2)])


def test_enroll_rejects_no_face_multiple_faces_and_garbage(provider):
    with pytest.raises(FaceError, match="No face"):
        provider.enroll([frame("alice", faces=0)])
    with pytest.raises(FaceError, match="More than one"):
        provider.enroll([frame("alice", faces=2)])
    with pytest.raises(FaceError, match="read"):
        provider.enroll([b"not json"])


def test_enroll_rejects_spoofed_photo(provider):
    with pytest.raises(FaceError, match="screen or print"):
        provider.enroll([frame("alice", seed=1, spoof=0.05)])


# ---------------------------------------------------------------- verify


def test_correct_challenge_passes(provider, alice):
    for action, direction in (("turn_left", 1), ("turn_right", -1)):
        r = provider.verify(alice, good_frames(direction=direction), challenge(action))
        assert r.verified, r
        assert r.reason == "ok"
        assert r.score > 0.9
        assert r.liveness_score == pytest.approx(0.95)
        assert r.checks["active"]["passed"] and r.checks["passive"]["passed"]


def test_wrong_direction_fails(provider, alice):
    r = provider.verify(alice, good_frames(direction=-1), challenge("turn_left"))
    assert not r.verified and r.reason == "challenge_failed"


def test_static_photo_identical_frames_fails(provider, alice):
    same = frame("alice", 0, seed=5)
    r = provider.verify(alice, [same, same, same], challenge())
    assert not r.verified and r.reason == "static_frames"


def test_photo_with_sensor_noise_but_no_turn_fails(provider, alice):
    frames = [frame("alice", 0, seed=s) for s in (20, 21, 22)]
    r = provider.verify(alice, frames, challenge())
    assert not r.verified and r.reason == "challenge_failed"


def test_identity_swap_between_frames_fails(provider, alice):
    frames = [frame("alice", 0, seed=1), frame("alice", 10, seed=2), frame("bob", 28, seed=3)]
    r = provider.verify(alice, frames, challenge())
    assert not r.verified and r.reason == "identity_changed"


def test_other_person_fails_match(provider, alice):
    r = provider.verify(alice, good_frames("bob"), challenge())
    assert not r.verified and r.reason == "face_mismatch"


def test_low_spoof_score_fails(provider, alice):
    r = provider.verify(alice, good_frames(spoof=0.3), challenge())
    assert not r.verified and r.reason == "spoof_detected"
    # one bad frame among good ones also fails (min-frame rule)
    frames = good_frames()
    frames[2] = frame("alice", 28, seed=12, spoof=0.2)
    assert provider.verify(alice, frames, challenge()).reason == "spoof_detected"


def test_passive_disabled_is_reported_not_silent(engine, alice):
    p = OpenCVProvider(engine, None, FaceConfig())
    r = p.verify(alice, good_frames(spoof=0.0), challenge())
    assert r.verified and r.checks["passive"] == "disabled"
    assert not p.passive_enabled


def test_expired_challenge_fails(provider, alice):
    old = Challenge(id="c", action="turn_left", issued_at=time.time() - 120, expires_in=60)
    r = provider.verify(alice, good_frames(), old)
    assert not r.verified and r.reason == "challenge_expired"


def test_bad_inputs(provider, alice):
    assert provider.verify(alice, good_frames()[:1], challenge()).reason == "bad_frame_count"
    assert provider.verify(alice, [frame("alice", faces=0)] * 2, challenge()).reason == "no_face"
    two = [frame("alice", seed=1), frame("alice", 25, seed=2, faces=2)]
    assert provider.verify(alice, two, challenge()).reason == "multiple_faces"
    assert provider.verify(alice, [b"x", b"y"], challenge()).reason == "bad_image"
    stranger = EnrolledFace(provider="opencv", embedding=None)
    assert provider.verify(stranger, good_frames(), challenge()).reason == "not_enrolled"
    other_model = EnrolledFace(provider="opencv", embedding=alice.embedding, model="old")
    assert provider.verify(other_model, good_frames(), challenge()).reason == "not_enrolled"


def test_blink_uses_eye_openness(provider, alice):
    open_ = [frame("alice", 0, seed=1, eyes=1.0), frame("alice", 0, seed=2, eyes=1.0)]
    closed = [*open_, frame("alice", 0, seed=3, eyes=0.0)]
    assert provider.verify(alice, closed, challenge("blink")).verified
    r = provider.verify(alice, open_, challenge("blink"))
    assert not r.verified and r.reason == "challenge_failed"


def test_bytearray_frames_are_wiped(provider, alice):
    frames = [bytearray(f) for f in good_frames()]
    assert provider.verify(alice, frames, challenge()).verified
    assert all(not any(f) for f in frames)


# ---------------------------------------------------------------- Azure (mocked)


def _azure(yaws=(0, 8, 28), identical=True, confidence=0.9):
    face_client = MagicMock()
    face_client.detect.side_effect = lambda *a, **k: [
        _az_face(yaws[face_client.detect.call_count - 1])
    ]
    face_client.verify_from_large_person_group.return_value = SimpleNamespace(
        is_identical=identical, confidence=confidence
    )
    admin = MagicMock()
    admin.large_person_group.create_person.return_value = SimpleNamespace(person_id="p-1")
    return (
        AzureProvider(group_id="g", face_client=face_client, admin_client=admin),
        face_client,
        admin,
    )


def _az_face(yaw):
    return SimpleNamespace(
        face_id="f", face_attributes=SimpleNamespace(head_pose=SimpleNamespace(yaw=yaw))
    )


def test_azure_enroll_stores_only_person_id():
    p, face, admin = _azure()
    e = p.enroll([b"a", b"b"])
    groups = admin.large_person_group
    assert e.provider == "azure" and e.external_id == "p-1" and e.embedding is None
    assert groups.add_face.call_count == 2
    groups.begin_train.assert_called_once()
    name = groups.create_person.call_args.kwargs["name"]
    assert len(name) == 32  # random uuid, no personal data sent


def test_azure_enroll_errors_and_cleanup():
    p, face, admin = _azure()
    face.detect.side_effect = None
    face.detect.return_value = []
    with pytest.raises(FaceError, match="No face"):
        p.enroll([b"a"])
    p, face, admin = _azure()
    face.detect.side_effect = None
    face.detect.return_value = [_az_face(0)]
    admin.large_person_group.add_face.side_effect = RuntimeError("x")
    with pytest.raises(FaceUnavailableError):
        p.enroll([b"a"])
    admin.large_person_group.delete_person.assert_called_once()


def test_azure_verify_pass_and_failures():
    e = EnrolledFace(provider="azure", external_id="p-1")
    p, *_ = _azure()
    r = p.verify(e, [b"1", b"2", b"3"], challenge("turn_left"))
    assert r.verified and r.checks["passive"] == "unavailable_on_azure"
    p, *_ = _azure()
    assert p.verify(e, [b"1", b"2", b"3"], challenge("turn_right")).reason == "challenge_failed"
    p, *_ = _azure(identical=False, confidence=0.2)
    r = p.verify(e, [b"1", b"2", b"3"], challenge("turn_left"))
    assert not r.verified and r.reason == "face_mismatch" and r.score == 0.2
    p, *_ = _azure()
    assert p.verify(e, [b"1", b"2"], challenge("blink")).reason == "challenge_failed"
    old = Challenge(id="c", action="turn_left", issued_at=time.time() - 99, expires_in=60)
    assert p.verify(e, [b"1", b"2"], old).reason == "challenge_expired"


def test_azure_http_errors_map():
    class Http(Exception):
        status_code = 400

    p, face, _ = _azure()
    face.detect.side_effect = Http("bad")
    with pytest.raises(FaceError):
        p.enroll([b"a"])
    face.detect.side_effect = RuntimeError("down")
    with pytest.raises(FaceUnavailableError):
        p.enroll([b"a"])
