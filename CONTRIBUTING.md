# Contributing

Thanks for your interest in improving llm-gateway. This is a small, self-hosted
OpenAI-compatible proxy; contributions that keep it lean and well-tested are very
welcome.

## Development setup

```bash
# create a virtualenv and install with dev extras
uv venv
uv pip install -e ".[dev,postgres,redis]"

# copy the env template and set at least GW_MASTER_KEY
cp .env.example .env

# run the dev server (zero-config SQLite by default)
uvicorn app.main:app --reload
```

The admin console is served at `/ui/`, the OpenAI-compatible API at `/v1/*`, and
the management API at `/admin/*`.

## Before you open a PR

Run the same checks CI runs:

```bash
ruff check app tests      # lint + import order
pytest -q                 # full test suite (uses SQLite + respx, no network)
```

Both must pass. New behavior needs a test — `tests/test_e2e.py` drives the whole
app through an ASGI transport with mocked upstreams (`respx`), which is the
preferred place for feature tests.

Dependencies are locked in `uv.lock` and CI installs with `uv sync --frozen`. If
you change `pyproject.toml` dependencies, regenerate and commit the lockfile:

```bash
uv lock
```

## Database changes

Models live in `app/db/models.py`. When you change a model, add an Alembic
migration so existing deployments can upgrade:

```bash
alembic revision -m "describe the change"   # then fill in upgrade()/downgrade()
alembic upgrade head
```

Keep `auto_create_tables` working for zero-config dev, but never rely on it in
production — the migration is the source of truth.

## Conventions

- Match the style of the surrounding code; keep functions small and typed.
- Secrets (`GW_MASTER_KEY`, `GW_ENCRYPTION_KEY`) and `.env` must never be
  committed. Credential API keys are encrypted at rest with Fernet.
- Prefer adding a setting (with a sensible default in `app/config.py` and a line
  in `.env.example`) over hard-coding operational behavior.
- User-facing strings in the console go through `t()` with a `zh` translation in
  `app/web/static/app.js`.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a tour of the request path,
routing, and the moving parts.
