"""Exceptions shared by the face package."""

from __future__ import annotations


class FaceError(Exception):
    """A user-facing problem with the submitted photo (no face, several faces...)."""


class FaceUnavailableError(Exception):
    """The face service could not be reached or rejected our credentials (maps to 503)."""


class ChallengeError(Exception):
    """A challenge token is malformed, tampered with, expired or for someone else.

    ``reason`` is one of: malformed, bad_signature, expired, not_yet_valid, wrong_subject.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SpoofModelMissingError(RuntimeError):
    """FACE_PASSIVE_LIVENESS=on but the anti-spoofing model files are not present."""
