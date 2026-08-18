"""Environment for the whole suite.

Settings are read once at import time and cached, so they must be set before anything
from `app` is imported. Fixtures that need Postgres or Redis live in tests/api/conftest.py --
tests/unit therefore runs anywhere, with nothing installed but Python.
"""

import os

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://linkly:linkly@localhost:5432/linkly_test"
    ),
)
# Database 15 by convention: the suite flushes it between tests, so never point this at dev data.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 32)
os.environ.setdefault("BASE_URL", "http://testserver")
