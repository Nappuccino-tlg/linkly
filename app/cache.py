import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.config import get_settings

settings = get_settings()

redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


@dataclass(frozen=True)
class CachedLink:
    """Everything the redirect path needs, so a cache hit never touches Postgres."""

    id: uuid.UUID
    target_url: str
    expires_at: datetime | None


def _key(code: str) -> str:
    return f"link:{code}"


async def get_link(code: str) -> CachedLink | None:
    raw = await redis.get(_key(code))
    if raw is None:
        return None
    data = json.loads(raw)
    expires_at = data["expires_at"]
    return CachedLink(
        id=uuid.UUID(data["id"]),
        target_url=data["target_url"],
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
    )


async def put_link(code: str, link: CachedLink) -> None:
    """Cache a resolvable link. Never outlives the link's own expiry."""
    ttl = settings.redirect_cache_ttl_seconds
    if link.expires_at is not None:
        remaining = int((link.expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            return
        ttl = min(ttl, remaining)

    payload = json.dumps(
        {
            "id": str(link.id),
            "target_url": link.target_url,
            "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        }
    )
    await redis.set(_key(code), payload, ex=ttl)


async def invalidate(code: str) -> None:
    """Must be called on every write that changes where a code points, or unpublishes it."""
    await redis.delete(_key(code))
