# Contributing

Issues and pull requests are welcome. This is a personal project, so the bar is "does it
make the thing better", not "does it match a process".

## Getting it running

```bash
cp .env.example .env
docker compose up --build
```

Or without Docker, with a Postgres and a Redis of your own:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

## Before opening a pull request

Run what CI runs:

```bash
ruff check . && ruff format --check . && pytest
```

`pytest` needs a real Postgres and a real Redis — see [Tests](README.md#tests). If you
only have Python to hand, `pytest tests/unit` covers the pure logic and needs nothing.

If you touched `app/models.py`, generate the migration and check it applies cleanly:

```bash
alembic revision --autogenerate -m "what changed"
alembic upgrade head && alembic check
```

CI runs `alembic check` too, so a model without a matching migration fails there.

## What the code is trying to be

The README's [Design notes](README.md#design-notes) are the short version. Two habits are
worth copying:

**Comments say why, not what.** `# increment the counter` earns nothing. `# INCR + EXPIRE
is atomic enough here because the key is per (bucket, window)` is why the next person does
not rewrite it. If a decision has a real cost, name the cost.

**Tests read as claims about behaviour.** `test_a_spoofed_forwarded_header_cannot_invent_visitors`
says what breaks if the code is wrong. `test_client_ip_2` does not.

Coverage is enforced at 95% in CI. That is a floor for noticing untested branches, not a
target to game — a test that asserts nothing counts the same as no test.

## Scope

Things likely to be accepted: bug fixes, a guard the code should have had, tests for a
path that has none, documentation that corrects something wrong.

Things worth opening an issue about first: new dependencies, a build step for the
dashboard, anything that changes the shape of the public API.
