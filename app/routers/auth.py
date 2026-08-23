from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import client_ip, get_current_user
from app.models import User
from app.ratelimit import consume, enforce_limit, identity_bucket, refund
from app.schemas import Token, UserCreate, UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate, request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    # Every attempt counts here, not just the failures: the thing being rationed is
    # account creation itself, and a successful one is exactly what a script wants.
    await enforce_limit(f"register:ip:{client_ip(request)}", settings.register_limit_per_hour)

    existing = await session.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/token", response_model=Token)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
) -> Token:
    """Exchange email and password for a bearer token.

    Throttled on two keys at once. Per-IP alone lets one attacker spread guesses for a
    single account across a botnet; per-email alone lets one host walk a password through
    a list of accounts. Neither is much use without the other.

    Both are spent before the password is checked and handed back if it was right, so a
    burst of parallel attempts cannot all slip through on the same stale count.
    """
    buckets = [
        f"login:ip:{client_ip(request)}",
        f"login:email:{identity_bucket(form.username)}",
    ]
    window = settings.login_window_seconds
    await consume(buckets, settings.login_limit_per_window, window)

    user = await session.scalar(select(User).where(User.email == form.username))
    # Same error for "no such user" and "wrong password" -- do not leak which emails exist.
    if user is None or not verify_password(form.password, user.password_hash):
        raise INVALID_CREDENTIALS

    # The attempt was the account's owner, so give the budget back. Ordinary use has to
    # cost nothing, or anyone could lock an account out by failing at it often enough.
    await refund(buckets, window)
    return Token(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
