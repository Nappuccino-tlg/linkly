import secrets

# base62, no separators -- safe anywhere in a URL path
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
DEFAULT_LENGTH = 7

# Codes the router itself owns, so a vanity code can never shadow a real route.
RESERVED_CODES = frozenset(
    {"api", "app", "auth", "docs", "redoc", "openapi.json", "healthz", "readyz", "static"}
)


def generate_code(length: int = DEFAULT_LENGTH) -> str:
    """Random base62 code.

    62**7 is ~3.5e12, so collisions are rare -- but "rare" is not "never", which is why
    the unique index on links.code is the real guarantee and the caller retries on conflict.
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
