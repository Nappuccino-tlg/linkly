import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    subject = decode_access_token(token)
    if subject is None:
        raise CREDENTIALS_ERROR
    try:
        user_id = uuid.UUID(subject)
    except ValueError as exc:
        raise CREDENTIALS_ERROR from exc

    user = await session.get(User, user_id)
    if user is None:
        raise CREDENTIALS_ERROR
    return user


def client_ip(request: Request) -> str | None:
    """Client address, honouring X-Forwarded-For when running behind a proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
