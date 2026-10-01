import uuid
from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import ServiceToken, User
from .security import (
    ACCESS,
    decode_token,
    hash_service_token,
    is_service_token,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_credentials_exc = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Не удалось проверить учётные данные",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _user_from_jwt(token: str, db: AsyncSession) -> User:
    try:
        payload = decode_token(token, expected_type=ACCESS)
        user_id = payload.get("sub")
        if user_id is None:
            raise _credentials_exc
    except jwt.PyJWTError:
        raise _credentials_exc

    user = await db.get(User, uuid.UUID(user_id))
    if user is None or not user.is_active:
        raise _credentials_exc
    return user


async def _user_from_service_token(token: str, db: AsyncSession) -> User:
    st = await db.scalar(
        select(ServiceToken).where(
            ServiceToken.token_hash == hash_service_token(token),
            ServiceToken.revoked.is_(False),
        )
    )
    if st is None:
        raise _credentials_exc
    user = await db.get(User, st.user_id)
    if user is None or not user.is_active:
        raise _credentials_exc
    # Best-effort usage stamp. Runs before the endpoint body, so nothing else is
    # pending in this session.
    st.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    return user


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Accept either a session JWT or a static service token (`svc_…`)."""
    if not token:
        raise _credentials_exc
    if is_service_token(token):
        return await _user_from_service_token(token, db)
    return await _user_from_jwt(token, db)


async def get_jwt_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Session (JWT) auth only — rejects static service tokens.

    Used where a service token must not be accepted, e.g. managing service
    tokens themselves, so a leaked token cannot mint or revoke others.
    """
    if not token or is_service_token(token):
        raise _credentials_exc
    return await _user_from_jwt(token, db)
