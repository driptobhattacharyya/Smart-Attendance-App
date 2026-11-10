"""Rotating desk identity: pure functions and the shared test vectors (see the architecture
appendix; the desk app test-suite uses the same numbers)."""

import os

import pytest

from app.places.identity import (
    WINDOW_SECONDS,
    match_devices,
    match_reading,
    rotating_ids,
    window_for,
)

SECRET = bytes(range(32))
VECTORS = [(0, 40716, 55737), (1, 50226, 57433), (5_000_000, 9808, 3354)]


@pytest.mark.parametrize(("window", "major", "minor"), VECTORS)
def test_fixed_vectors(window, major, minor):
    assert rotating_ids(SECRET, window) == (major, minor)


def test_ids_are_16_bit_and_deterministic():
    for w in range(200):
        major, minor = rotating_ids(SECRET, w)
        assert 0 <= major <= 65535 and 0 <= minor <= 65535
        assert rotating_ids(SECRET, w) == (major, minor)


def test_different_secrets_and_windows_differ():
    other = bytes(reversed(range(32)))
    assert rotating_ids(SECRET, 7) != rotating_ids(other, 7)
    assert len({rotating_ids(SECRET, w) for w in range(50)}) > 45


def test_window_boundaries():
    assert window_for(0) == 0 and window_for(299.9) == 0 and window_for(300) == 1
    assert window_for(1_500_000_000) == 5_000_000


def test_match_accepts_previous_current_and_next_window():
    t = 5_000_000 * WINDOW_SECONDS + 10  # inside window 5_000_000
    assert match_reading(SECRET, 9808, 3354, t) == 5_000_000
    prev = rotating_ids(SECRET, 4_999_999)
    nxt = rotating_ids(SECRET, 5_000_001)
    assert match_reading(SECRET, *prev, t) == 4_999_999
    assert match_reading(SECRET, *nxt, t) == 5_000_001


def test_match_rejects_two_windows_away_wrong_secret_and_wrong_ids():
    t = 5_000_000 * WINDOW_SECONDS + 10
    assert match_reading(SECRET, *rotating_ids(SECRET, 4_999_998), t) is None
    assert match_reading(SECRET, *rotating_ids(SECRET, 5_000_002), t) is None
    assert match_reading(os.urandom(32), 9808, 3354, t) is None
    assert match_reading(SECRET, 9808, 3355, t) is None


def test_match_works_with_datetimes():
    from datetime import UTC, datetime

    at = datetime.fromtimestamp(1 * WINDOW_SECONDS + 1, tz=UTC)
    assert match_reading(SECRET, 50226, 57433, at) == 1


def test_match_at_epoch_start_does_not_go_negative():
    assert match_reading(SECRET, 40716, 55737, 5) == 0


def test_match_devices_returns_all_colliding_candidates():
    same = bytes(range(32))
    cands = [("a", SECRET), ("b", os.urandom(32)), ("c", same)]
    assert match_devices(cands, 40716, 55737, 10) == ["a", "c"]
