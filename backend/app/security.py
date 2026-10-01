import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from .config import settings

ACCESS = "access"
REFRESH = "refresh"

# Prefix on raw service tokens so the auth dependency can route them without a
# wasted JWT-decode attempt. Part of the token value (and of its hash input).
SERVICE_TOKEN_PREFIX = "svc_"


def generate_service_token() -> str:
    """A new opaque, high-entropy service token (shown to the client once)."""
    return SERVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_service_token(raw: str) -> str:
    """SHA-256 hex of a raw service token — what we store and look up by."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_service_token(raw: str) -> bool:
    return raw.startswith(SERVICE_TOKEN_PREFIX)


def _to_bytes(password: str) -> bytes:
    # bcrypt operates on at most 72 bytes; truncate to stay within that limit.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(subject: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str) -> str:
    return _create_token(
        subject, ACCESS, timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(subject: str) -> str:
    return _create_token(
        subject, REFRESH, timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload
