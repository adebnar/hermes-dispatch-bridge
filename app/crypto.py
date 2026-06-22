"""Optional end-to-end encryption of push payloads.

When the app registers a 32-byte AES-256 key, the bridge encrypts the JSON push
body with AES-GCM before POSTing it to the device's UnifiedPush endpoint, so the
push relay (e.g. ntfy.sh) only ever sees ciphertext. The app decrypts locally
and shows its own notification.

Wire format (what we POST as the message body when a key is set):

    HDX1.<base64url(nonce)>.<base64url(ciphertext+tag)>

The "HDX1." prefix lets the client distinguish encrypted from plaintext bodies
(the plaintext ntfy-topic path is unchanged for users who read alerts in the
ntfy app directly).
"""

from __future__ import annotations

import base64
import os

PREFIX = "HDX1."


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_key(key_b64: str) -> bytes | None:
    """Decode a base64 (standard or urlsafe) 32-byte key, or None if invalid."""
    if not key_b64:
        return None
    s = key_b64.strip()
    pad = "=" * (-len(s) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decoder(s + pad)
        except (ValueError, base64.binascii.Error):
            continue
        if len(raw) == 32:
            return raw
    return None


def encrypt(plaintext: bytes, key: bytes) -> str:
    """AES-GCM encrypt and return the wire string ``HDX1.<nonce>.<ct>``."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return f"{PREFIX}{_b64u(nonce)}.{_b64u(ct)}"
