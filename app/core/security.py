from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet

from app.config import get_settings

_settings = get_settings()


def _fernet() -> Fernet:
    """Build a Fernet from the configured key, or derive one from master_key."""
    raw = _settings.encryption_key or _settings.master_key
    # Fernet needs a 32-byte urlsafe-base64 key; derive deterministically.
    digest = hashlib.sha256(raw.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


# --- Virtual key helpers ---

KEY_PREFIX = "sk-gw-"


def generate_virtual_key() -> str:
    """Generate a new opaque virtual key shown to the user exactly once."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    """Deterministic hash for storage + lookup."""
    return hashlib.sha256(key.encode()).hexdigest()


def key_display_prefix(key: str) -> str:
    return key[: len(KEY_PREFIX) + 4]
