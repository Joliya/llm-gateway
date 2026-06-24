## What & why

What does this change, and what problem does it solve? Link any related issue
(`Closes #123`).

## How

Brief notes on the approach — anything a reviewer should know about the design.

## Checklist

- [ ] `ruff check app tests` passes
- [ ] `pytest -q` passes (new behavior has a test, preferably in `tests/test_e2e.py`)
- [ ] DB model changes include an Alembic migration
- [ ] New settings have a default in `app/config.py` and a line in `.env.example`
- [ ] User-facing console strings go through `t()` with a `zh` translation
- [ ] `uv.lock` regenerated if `pyproject.toml` dependencies changed
- [ ] No secrets, real API keys, or `.env` committed
