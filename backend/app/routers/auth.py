import secrets
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import get_current_user, get_jwt_user
from ..models import ServiceToken, User
from ..schemas import (
    RefreshRequest,
    ServiceTokenCreate,
    ServiceTokenCreated,
    ServiceTokenOut,
    TokenPair,
    UserLogin,
    UserOut,
    UserRegister,
)
from ..security import (
    REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_service_token,
    hash_password,
    hash_service_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _tokens_for(user: User) -> TokenPair:
    sub = str(user.id)
    return TokenPair(
        access_token=create_access_token(sub),
        refresh_token=create_refresh_token(sub),
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)) -> TokenPair:
    email = payload.email.lower().strip()
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже существует",
        )
    user = User(
        email=email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _tokens_for(user)


@router.post("/guest", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def guest(db: AsyncSession = Depends(get_db)) -> TokenPair:
    """Start an anonymous session — no registration needed.

    The guest owns its jobs like any user, so every existing permission check
    keeps working. Password is random and unusable: nobody can log in as a
    guest, the session lives only through its tokens. Guests and their results
    are purged by the cleanup janitor.
    """
    # example.com is IANA-reserved for documentation: clearly synthetic, yet a
    # syntactically valid address (".local" is rejected as a reserved domain).
    user = User(
        email=f"guest-{uuid.uuid4()}@guest.example.com",
        full_name="Гость",
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        is_guest=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _tokens_for(user)


@router.post("/login", response_model=TokenPair)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> TokenPair:
    email = payload.email.lower().strip()
    user = await db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Аккаунт отключён")
    return _tokens_for(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    try:
        data = decode_token(payload.refresh_token, expected_type=REFRESH)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный refresh-токен"
        )
    user = await db.get(User, uuid.UUID(data["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")
    return _tokens_for(user)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


# ---- Service tokens (machine integrations) ---------------------------------
# Managed with a session JWT only (get_jwt_user), so a leaked service token
# cannot mint or revoke tokens — it can only use the jobs endpoints.

@router.post(
    "/service-token",
    response_model=ServiceTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_service_token(
    payload: ServiceTokenCreate,
    user: User = Depends(get_jwt_user),
    db: AsyncSession = Depends(get_db),
) -> ServiceTokenCreated:
    if user.is_guest:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Гостевой аккаунт не может выпускать сервисные токены",
        )
    raw = generate_service_token()
    st = ServiceToken(
        user_id=user.id,
        token_hash=hash_service_token(raw),
        name=(payload.name or "").strip(),
    )
    db.add(st)
    await db.commit()
    await db.refresh(st)
    out = ServiceTokenCreated.model_validate(st)
    out.token = raw  # shown once
    return out


@router.get("/service-tokens", response_model=list[ServiceTokenOut])
async def list_service_tokens(
    user: User = Depends(get_jwt_user),
    db: AsyncSession = Depends(get_db),
) -> list[ServiceToken]:
    rows = await db.scalars(
        select(ServiceToken)
        .where(ServiceToken.user_id == user.id)
        .order_by(ServiceToken.created_at.desc())
    )
    return list(rows)


@router.delete("/service-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_service_token(
    token_id: uuid.UUID,
    user: User = Depends(get_jwt_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    st = await db.scalar(
        select(ServiceToken).where(
            ServiceToken.id == token_id, ServiceToken.user_id == user.id
        )
    )
    if st is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Токен не найден")
    st.revoked = True
    await db.commit()
