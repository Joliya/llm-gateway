from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

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


# --- User password helpers (pbkdf2, stdlib only) ---

_PBKDF2_ITERATIONS = 200_000


def generate_password(length: int = 16) -> str:
    """Auto-generate a human-typable password shown to the operator once."""
    return secrets.token_urlsafe(length)


def hash_password(password: str) -> str:
    """Salted PBKDF2-SHA256 hash, stored as `pbkdf2$iters$salt$hash` (all b64)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode()  # noqa: E731
    return f"pbkdf2${_PBKDF2_ITERATIONS}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


# --- User session tokens (stateless, HMAC-signed with master_key) ---
#
# A login issues `<payload>.<sig>` where payload is base64url(json{uid,exp}) and
# sig = HMAC-SHA256(master_key, payload). No server-side session store: tokens
# are verified by signature + expiry, and revoked by disabling/deleting the user
# (callers re-check the user row) or rotating GW_MASTER_KEY.

SESSION_PREFIX = "gws-"


def _sign(payload_b64: str) -> str:
    return hmac.new(
        _settings.master_key.encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()


def issue_session_token(user_id: int, ttl_seconds: int) -> tuple[str, int]:
    """Return (token, expires_at_epoch) for a logged-in user."""
    exp = int(time.time()) + ttl_seconds
    payload = base64.urlsafe_b64encode(
        json.dumps({"uid": user_id, "exp": exp}).encode()
    ).decode()
    return f"{SESSION_PREFIX}{payload}.{_sign(payload)}", exp


def verify_session_token(token: str) -> int | None:
    """Return the user id if the token is well-formed, signed and unexpired."""
    if not token.startswith(SESSION_PREFIX):
        return None
    try:
        payload, sig = token[len(SESSION_PREFIX):].split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        if int(data["exp"]) < int(time.time()):
            return None
        return int(data["uid"])
    except (ValueError, TypeError, KeyError):
        return None
