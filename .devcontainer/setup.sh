#!/usr/bin/env bash
# Everything a Codespace needs before the app can start. Runs once, at create time.
set -euo pipefail

echo "==> installing linkly and its dev dependencies"
# Root is fine here: the container is the environment, so a venv inside it buys nothing.
pip install --quiet --no-cache-dir --root-user-action=ignore -e ".[dev]"

echo "==> creating the test database"
# CREATE DATABASE cannot run inside a transaction and has no IF NOT EXISTS, so the
# already-there case is caught rather than avoided. psql is not in this image; asyncpg
# is, because the application depends on it.
python - <<'PY'
import asyncio

import asyncpg


async def main() -> None:
    conn = await asyncpg.connect("postgresql://linkly:linkly@db:5432/linkly")
    try:
        await conn.execute("CREATE DATABASE linkly_test OWNER linkly")
        print("    created linkly_test")
    except asyncpg.DuplicateDatabaseError:
        print("    linkly_test already exists")
    finally:
        await conn.close()


asyncio.run(main())
PY

echo "==> running migrations"
alembic upgrade head

cat <<'EOF'

==> ready

    The app starts on port 8000 when this terminal attaches; the dashboard is at /app/.
    Create an account, shorten something, open the short link, and the click counter
    moves. Nothing here is reachable from outside your own Codespace.

    pytest              the whole suite, against the real Postgres and Redis
    pytest tests/unit   just the pure logic

EOF
