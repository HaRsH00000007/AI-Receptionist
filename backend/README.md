# AI Receptionist — backend

FastAPI service. Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Setup

```bash
cp ../.env.example ../.env      # then edit
uv sync
```

## Run

```bash
docker compose -f ../docker-compose.yml up -d db    # local Postgres 16 on :55432
uv run alembic upgrade head
uv run uvicorn app.asgi:app --reload --port 8000
```

> The container publishes **55432**, not 5432. A native PostgreSQL install
> commonly already holds 5432 (and 5433 if it runs two clusters), and the clash
> is silent: Docker still reports the port as published, but connections reach
> the other server and fail with "password authentication failed". Change
> `POSTGRES_PORT` in `.env` if 55432 is taken too.

- <http://localhost:8000/healthz> — liveness
- <http://localhost:8000/readyz> — readiness
- <http://localhost:8000/docs> — OpenAPI (hidden in staging/production)

## Migrations

```bash
uv run alembic upgrade head                              # apply
uv run alembic revision --autogenerate -m "add x"        # generate, then REVIEW
uv run alembic downgrade -1                              # step back
uv run alembic current                                   # where am I
```

The URL comes from `DATABASE_URL`, never from `alembic.ini` — no credential is
written to a committed file. Always read a generated migration before applying
it; autogenerate is a first draft, not an answer.

## Validate

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests migrations
uv run pytest
```

The database tests need PostgreSQL. They build a scratch database
(`receptionist_test`) by **running the migrations**, so a model changed without
a migration fails here rather than in production. With no database reachable
they skip, naming the reason. Point them elsewhere with `TEST_DATABASE_URL`.

## Layout

See the root README for the full tree. The parts that shape everything else:

```
app/
  main.py             create_app() factory, middleware, error envelope
  worker.py           the polling worker
  provisioning/       the state machine (engine, steps, retry, compensation)
  providers/          protocols + fakes + real adapters; registry picks
  services/           signup, config generation, summarization, webhooks
  core/               settings, logging, correlation ids, error taxonomy
```

## Running the worker

```bash
uv run python -m app.worker
```

Two loops in one process: provisioning runs and post-call processing. Both claim
work with `FOR UPDATE SKIP LOCKED` plus a lease, so several worker processes can
run side by side and a crashed one hands its work back when the lease expires.

## Conventions

- **No module-level `app`, no global settings.** Everything is built by
  `create_app(settings)`. This is what lets tests configure an app without
  touching the environment.
- **Every error is retryable or terminal**, declared by the exception class.
  The provisioning engine reads that flag; it never inspects vendor exception
  types. Never retry a 400.
- **Every log line carries `correlation_id`.** Pass structured context via
  `extra=`, not by formatting it into the message.
- **`DRY_RUN` defaults to `true`** and gates every side effect that costs money.
- **Mixins come before `Base`** in a model's bases, so `UUIDPrimaryKeyMixin`
  assigns the id at construction. A provisioning step can then derive an
  idempotency key, and link child rows, before anything is flushed.
- **Enums are VARCHAR + CHECK**, not native Postgres types, so adding a
  provisioning step later is a column change rather than an `ALTER TYPE`.
  Their values are persisted: renaming one is a migration, not a refactor.
- **Constraints, not conventions.** One live number per tenant, one live run per
  tenant, one step per run, one call per `provider_call_id` — all enforced by
  partial unique indexes, so an application bug cannot spend money twice.
