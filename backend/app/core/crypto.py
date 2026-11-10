"""AES-GCM encryption for secrets stored in the database (per-device keys, MFA secrets)."""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12


class DecryptionError(Exception):
    pass


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """``nonce || ciphertext+tag``. ``aad`` binds the blob to its row (e.g. the device id)."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError("Could not decrypt value") from exc
