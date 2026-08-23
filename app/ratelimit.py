import hashlib
import time
from collections.abc import Sequence

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


# DECR only where the window still exists. A plain DECR against a key that expired
# between the spend and the refund would recreate it at -1 with no TTL, and that key
# would then quietly absorb the next window's failures.
_REFUND = """
for _, key in ipairs(KEYS) do
  if redis.call('EXISTS', key) == 1 then
    redis.call('DECR', key)
  end
end
return 1
"""

_refund_script = redis.register_script(_REFUND)


async def consume(buckets: Sequence[str], limit: int, window_seconds: int) -> None:
    """Spend one from every bucket at once, then reject if any of them is now over.

    Spending before deciding is the whole point. A limiter that reads the counter, does
    slow work, and increments afterwards can be walked straight past: a hundred attempts
    fired together all read the same low count and all pass, so the limit stops meaning
    anything the moment an attacker stops being polite and sends them in parallel. INCR
    is atomic, so those hundred attempts get a hundred distinct numbers instead.

    All the buckets are spent even when one of them is what refuses the request. Refusing
    on the cheaper key first would leave the other unspent, and the difference is worth
    less than the extra round trip it would cost to be exact about it.
    """
    async with redis.pipeline(transaction=True) as pipe:
        for bucket in buckets:
            key = _key(bucket, window_seconds)
            pipe.incr(key)
            pipe.expire(key, window_seconds)
        replies = await pipe.execute()

    # Every other reply is an INCR; the EXPIREs in between are bookkeeping.
    if any(count > limit for count in replies[0::2]):
        raise _too_many(window_seconds)


async def refund(buckets: Sequence[str], window_seconds: int) -> None:
    """Hand back what an attempt spent, once it turns out to have been a legitimate one.

    Consume-then-refund rather than check-then-consume: it costs a round trip on the
    happy path and buys a limiter that a burst cannot walk past.
    """
    await _refund_script(keys=[_key(bucket, window_seconds) for bucket in buckets])
