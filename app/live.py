"""The live click feed: one Redis connection per instance, however many watchers.

A click is recorded by whichever instance served the redirect. The dashboard watching for
it is connected to whichever instance the load balancer happened to pick. Those are only
the same process by luck, which is why this goes through Redis rather than a module-level
set of open sockets -- with more than one worker, that set is wrong on every instance but
the lucky one, and it is wrong silently.

The fan-out itself is hubcast: https://github.com/Nappuccino-tlg/hubcast
"""

import json
import logging
from datetime import datetime
from urllib.parse import urlparse

from hubcast import Hub, Overflow

from app.cache import redis

log = logging.getLogger(__name__)

#: One Hub for the process. It holds a single Redis connection whatever the number of
#: dashboards attached to it, which is the reason for using it rather than a subscription
#: per WebSocket: a thousand open dashboards would otherwise be a thousand Redis
#: connections, and the server runs out of file descriptors long before that is
#: interesting traffic.
#:
#: DROP_OLDEST because this is a counter, not a log. A dashboard that falls behind wants
#: the number as it is now; replaying a stale backlog at it would only show it the past.
hub = Hub(redis, prefix="linkly", max_queue=100, overflow=Overflow.DROP_OLDEST)


def topic(code: str) -> str:
    """One topic per short code, so an instance subscribes only to links someone is watching."""
    return f"link:{code}"


def _referrer_host(referrer: str | None) -> str | None:
    """Just the host.

    A full Referer is a URL on somebody else's site, and it can carry a path and a query
    string that were never meant to travel -- a search term, a session id, an unlisted
    page. The host is the part that answers "where is this traffic coming from", which is
    the only part the feed is for.
    """
    if not referrer:
        return None
    return urlparse(referrer).netloc or None


async def publish_click(code: str, clicked_at: datetime, referrer: str | None) -> None:
    """Announce a click to every dashboard watching this code, on every instance.

    Best effort on purpose. The click is already committed to Postgres by the time this
    runs, so a Redis that is down costs a live update and nothing else -- and a redirect
    must not fail, or even log a stack trace, because a cosmetic feed could not be
    delivered. Analytics durability is the database's job; this is the nice-to-have on top.
    """
    payload = json.dumps(
        {"code": code, "at": clicked_at.isoformat(), "referrer": _referrer_host(referrer)}
    )
    try:
        await hub.publish(topic(code), payload)
    # Broad on purpose, and the docstring says why: a redirect must not fail, or even
    # log a stack trace, because a cosmetic feed could not be delivered.
    except Exception as exc:
        log.warning("live click feed unavailable: %s", type(exc).__name__)
