"""Environment for the whole suite.

Settings are read once at import time and cached, so they must be set before anything
from `app` is imported. Fixtures that need Postgres or Redis live in tests/api/conftest.py --
tests/unit therefore runs anywhere, with nothing installed but Python.

The first three are assigned rather than defaulted, and that is deliberate. Between tests
this suite truncates every table and flushes a Redis database, so what it points at is not
something the surrounding shell gets a vote on. Anyone whose environment already exports
DATABASE_URL -- a devcontainer, a sourced .env, a terminal left over from running the app
-- would otherwise watch the suite empty the database they were working in, and the first
symptom would be missing data rather than a failing test.
"""

import os

# TEST_DATABASE_URL is how you name the database the suite is allowed to destroy.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://linkly:linkly@localhost:5432/linkly_test"
)
# Database 15 by convention: the suite flushes it between tests.
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
# Not negotiable: the test client calls itself testserver, and the self-reference guard
# compares a target against BASE_URL. If they disagree, that guard is never exercised.
os.environ["BASE_URL"] = "http://testserver"

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 32)
# The suite exercises the behind-a-proxy configuration, which is the one that runs in
# production. tests/unit/test_deps.py covers the directly-exposed case on its own.
os.environ.setdefault("TRUSTED_PROXY_HOPS", "1")
# Sign-in throttling would otherwise trip partway through a suite that logs in constantly.
os.environ.setdefault("LOGIN_LIMIT_PER_WINDOW", "500")
os.environ.setdefault("REGISTER_LIMIT_PER_HOUR", "500")
