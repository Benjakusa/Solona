import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(user_id: UUID, session_id: UUID) -> tuple[str, str]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token_id = secrets.token_urlsafe(18)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": token_id,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)
    return token, token_id


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        get_settings().jwt_secret.get_secret_value(),
        algorithms=[_ALGORITHM],
        options={"require": ["sub", "sid", "jti", "typ", "iat", "exp"]},
    )
