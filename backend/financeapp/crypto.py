"""Encryption for secrets kept in the database: aggregator tokens, API keys, TOTP seeds.

AES-256-GCM with a random 96-bit nonce per value and the column's name as associated data, so a ciphertext copied
from one column (or row kind) to another fails to decrypt instead of being misread. The master key comes from
ENCRYPTION_KEY in .env and never touches the database or the backups.

Stored format: "v1:" + base64url(nonce || ciphertext || tag). The version prefix leaves room for key rotation.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "v1:"


class MasterKeyError(RuntimeError):
    """The master key is missing or malformed."""


class DecryptError(RuntimeError):
    """A stored value didn't decrypt: wrong key, wrong column, or tampered with."""


def parse_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        raise MasterKeyError("ENCRYPTION_KEY is not valid base64 (make one with: openssl rand -base64 32)") from e
    if len(key) != 32:
        raise MasterKeyError("ENCRYPTION_KEY must be 32 bytes (make one with: openssl rand -base64 32)")
    return key


_key: AESGCM | None = None


def configure(value: str) -> None:
    """Loads the master key. Called once at startup; an empty value leaves encryption unavailable."""
    global _key
    _key = AESGCM(parse_key(value)) if value else None


def key_configured() -> bool:
    return _key is not None


def _aead() -> AESGCM:
    if _key is None:
        raise MasterKeyError("ENCRYPTION_KEY is not set (make one with: openssl rand -base64 32)")
    return _key


def encrypt(plaintext: str, purpose: str) -> str:
    nonce = os.urandom(12)
    sealed = _aead().encrypt(nonce, plaintext.encode(), purpose.encode())
    return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt(stored: str, purpose: str) -> str:
    if not stored.startswith(PREFIX):
        raise DecryptError("unknown format")
    try:
        raw = base64.urlsafe_b64decode(stored[len(PREFIX) :])
        return _aead().decrypt(raw[:12], raw[12:], purpose.encode()).decode()
    except (InvalidTag, ValueError, binascii.Error) as e:
        raise DecryptError("could not decrypt") from e
