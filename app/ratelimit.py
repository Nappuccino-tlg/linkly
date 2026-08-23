import hashlib
import time

from fastapi import HTTPException, status

from app.cache import redis


def _key(bucket: str, window_seconds: int) -> str:
    window = int(time.time()) // window_seconds
    return f"ratelimit:{bucket}:{window}"


def _too_many(window_seconds: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Rate limit exceeded. Try again later.",
        headers={"Retry-After": str(window_seconds)},
    )


def identity_bucket(value: str) -> str:
    """A fixed-width, log-safe stand-in for a caller-supplied identifier.

    Emails key the sign-in limiter, and an email is both unbounded in length and personal
    data. Hashing gives a bounded Redis key and keeps the address out of anything that
    later dumps the keyspace.
    """
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:32]


async def enforce_limit(bucket: str, limit: int, window_seconds: int = 3600) -> None:
    """Count this call against the bucket, and reject it if that puts it over.

    INCR + EXPIRE is atomic enough here because the key is per (bucket, window): the worst
    case is a burst straddling a window boundary allowing up to 2x the limit. A sliding
    window would fix that at the cost of a sorted set per client -- not worth it yet.
    """
    key = _key(bucket, window_seconds)

    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = await pipe.execute()

    if count > limit:
        raise _too_many(window_seconds)


async def check_limit(bucket: str, limit: int, window_seconds: int) -> None:
    """Reject if the bucket is already spent, without spending from it.

    Paired with record_failure for sign-in: only a failed attempt costs anything, so
    someone signing in correctly forty times in a row is never locked out, while someone
    guessing gets ten tries.
    """
    current = await redis.get(_key(bucket, window_seconds))
    if current is not None and int(current) >= limit:
        raise _too_many(window_seconds)


async def record_failure(bucket: str, window_seconds: int) -> None:
    """Spend one from the bucket. The reject happens on the next check_limit."""
    key = _key(bucket, window_seconds)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        await pipe.execute()
