import ipaddress
import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User
from app.security import decode_access_token

settings = get_settings()

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
    """The caller's address, and only an address this server has a reason to believe.

    X-Forwarded-For is a request header like any other: anyone talking to the app
    directly can put whatever they like in it. Since this value keys the per-IP rate
    limit and seeds the unique-visitor hash, trusting it unconditionally hands both away
    -- a new value per request is an unlimited quota and an unlimited visitor count.

    So it is read only when TRUSTED_PROXY_HOPS says a proxy is actually in front, and
    then only the entry that proxy appended. Each hop appends the address it saw, so with
    one proxy the client is the last entry; entries to the left of it are whatever the
    client sent and are never used.
    """
    peer = request.client.host if request.client else None

    hops = settings.trusted_proxy_hops
    if hops < 1:
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer

    entries = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(entries) < hops:
        # Fewer hops than configured means the chain is not the one we were told to
        # expect. Fall back to the peer rather than pick an entry the client controls.
        return peer

    candidate = entries[-hops]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return peer
    return candidate
