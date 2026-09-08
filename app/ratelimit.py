"""The limiters this application wants, and the 429 it answers with.

This module used to be the limiter itself: the Lua script, the atomic multi-key spend, the
refund that had to check the window still existed before crediting it back. None of that
was ever specific to short links, and getting it right here meant getting it right in
exactly one place while every other service repeated the same mistakes. So it moved out
into `redlimit`, and what is left is configuration.

    https://github.com/Nappuccino-tlg/redlimit
"""

from collections.abc import Sequence

from fastapi import HTTPException, status
from redlimit import FixedWindow, Limiter, hashed

from app.cache import redis
from app.config import get_settings

settings = get_settings()

HOUR = 3600

# Kept under its own name so the call sites read the same as they did.
identity_bucket = hashed

# Failed sign-ins, spent on the address and the account at once -- see app/routers/auth.py
# for why one key alone is not worth having.
sign_in = FixedWindow(
    redis,
    limit=settings.login_limit_per_window,
    window=settings.login_window_seconds,
    prefix="linkly:login",
)

registration = FixedWindow(
    redis, limit=settings.register_limit_per_hour, window=HOUR, prefix="linkly:register"
)

link_creation = FixedWindow(
    redis, limit=settings.create_limit_per_hour, window=HOUR, prefix="linkly:create"
)

# An address gets more than one account's worth, because an office or a phone network is
# one address to us and many people to itself.
link_creation_by_address = FixedWindow(
    redis,
    limit=settings.create_limit_per_hour * 3,
    window=HOUR,
    prefix="linkly:create-ip",
)


async def enforce(limiter: Limiter, keys: Sequence[str] | str) -> None:
    """Spend from `limiter`, and turn a refusal into this API's 429.

    Retry-After comes from the decision rather than from the window length: telling a
    caller to wait a full hour when the window has four minutes left is the kind of advice
    that gets ignored, and then retried immediately.
    """
    decision = await limiter.consume(keys)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
            headers={"Retry-After": str(max(1, round(decision.retry_after)))},
        )
