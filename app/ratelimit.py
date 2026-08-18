import time

from fastapi import HTTPException, status

from app.cache import redis


async def enforce_limit(bucket: str, limit: int, window_seconds: int = 3600) -> None:
    """Fixed-window counter.

    INCR + EXPIRE is atomic enough here because the key is per (bucket, window): the worst
    case is a burst straddling a window boundary allowing up to 2x the limit. A sliding
    window would fix that at the cost of a sorted set per client -- not worth it yet.
    """
    window = int(time.time()) // window_seconds
    key = f"ratelimit:{bucket}:{window}"

    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = await pipe.execute()

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
            headers={"Retry-After": str(window_seconds)},
        )
