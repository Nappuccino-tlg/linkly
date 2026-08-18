"""Checks a shortener has to make before it will point at a URL.

A link shortener is an open redirector by definition, which makes it attractive for
phishing and for probing whatever network the server happens to sit in. These are the
cheap, deterministic guards: they need no DNS lookup, so they cannot be turned into a
timing side-channel or a way to make the server issue outbound requests.

What this deliberately does NOT do is resolve hostnames. Catching `evil.com` when it
resolves to 127.0.0.1 needs a resolve-then-pin at redirect time, which is a different
and much heavier design. The guard below is the floor, not the ceiling.
"""

import ipaddress
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Suffixes that only ever mean "somewhere inside this network".
BLOCKED_SUFFIXES = ("localhost", ".localhost", ".local", ".internal", ".home.arpa")

MAX_URL_LENGTH = 2048


class UnsafeTargetError(ValueError):
    """The URL is syntactically fine but must not be shortened."""


def _blocked_address(host: str) -> bool:
    """True when the host is an IP literal pointing somewhere it should not."""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def check(url: str) -> None:
    """Raise UnsafeTargetError if this URL must not be handed out as a short link."""
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeTargetError("URL is too long")

    parts = urlparse(url)

    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeTargetError("Only http and https URLs can be shortened")

    if not parts.hostname:
        raise UnsafeTargetError("URL has no host")

    # http://apple.com@evil.example reads as Apple to a human and resolves to evil.example.
    # Nobody needs credentials in a shared link, so refuse the whole shape.
    if parts.username or parts.password:
        raise UnsafeTargetError("URLs containing credentials cannot be shortened")

    host = parts.hostname.lower().rstrip(".")

    if host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeTargetError("URL points inside a private network")

    if _blocked_address(host):
        raise UnsafeTargetError("URL points at a private or loopback address")


def is_safe(url: str) -> bool:
    try:
        check(url)
    except UnsafeTargetError:
        return False
    return True
