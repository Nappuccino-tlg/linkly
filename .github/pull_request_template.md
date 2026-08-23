## What this changes

<!-- And why. If it fixes an issue, "Fixes #123" here. -->

## How it was verified

<!-- Which tests, or what you ran by hand. "CI is green" is fine when CI covers it. -->

- [ ] `ruff check . && ruff format --check .`
- [ ] `pytest` against a real Postgres and Redis
- [ ] `alembic check` if `app/models.py` changed
