"""Password hashing utilities.

Uses PBKDF2-HMAC-SHA256 (stdlib hashlib) instead of bcrypt so the app has
no compiled C-extension dependency to worry about when frozen with PyInstaller.
"""
import hashlib
import hmac
import os

_ITERATIONS = 200_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return a 'salt$hash' string (both hex-encoded) for storage in the DB."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Check a plaintext password against a stored 'salt$hash' string."""
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(expected, actual)
