# Local production setup

The production architecture, running on your machine. Not a reduced "dev mode"
— the same PostgreSQL, Redis, Temporal, S3 API and SMTP interface the deployed
system uses. Moving to AWS changes endpoints and credentials, not code.

Everything here runs with **fake vendors and `DRY_RUN=true`**. Nothing can spend
money, call a phone, or email a real person until you deliberately turn that off
(§6).

---

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Docker Desktop | 4.x, with Compose v2 | `docker compose version` |
| uv | ≥ 0.5 | `uv --version` |
| Node.js | 24.x | `node --version` |
| Git | any | `git --version` |

### Windows notes

Docker Desktop needs a virtualization backend. On Windows 11 that means WSL2:

```powershell
wsl --install          # then reboot
```

If `wsl --install` reports that virtualization is disabled, enable
Virtualization / SVM / VT-x in the BIOS first. After the reboot, install Docker
Desktop and confirm Settings → General → *Use the WSL 2 based engine* is ticked.

If `uv` is installed but not found, it is at `%USERPROFILE%\.local\bin\uv.exe`;
add that directory to `PATH`.

---

## 2. First run

```bash
git clone <repo> && cd AI_Receptionist

# A local stack that cannot bill you. Everything is pre-filled.
cp .env.local.example .env

# PostgreSQL, Redis, Temporal (+UI), MinIO, Mailpit.
docker compose up -d

# Wait for health. All services should read "healthy" or "running".
docker compose ps
```

First boot takes a few minutes: Temporal creates its schema, and MinIO's
init container creates the bucket and exits (`Exited (0)` is success, not a
failure).

```bash
# Schema. PostgreSQL is the source of truth, so this is the step that
# actually creates the system.
cd backend
uv sync
uv run alembic upgrade head

# API
uv run uvicorn app.asgi:app --reload --port 8000

# Temporal worker, in a second terminal. This is what drives provisioning.
cd backend && uv run python -m app.temporal.worker

# Polling worker, in a third. Post-call processing only — under
# ORCHESTRATOR=temporal it deliberately does not claim provisioning runs.
cd backend && uv run python -m app.worker

# Frontend, in a fourth
cd frontend && npm ci && npm run dev
```

### What is now running

| Service | URL | Notes |
|---|---|---|
| Frontend | http://localhost:3000 | |
| API | http://localhost:8000 | |
| API docs | http://localhost:8000/docs | Hidden in staging/production |
| Liveness | http://localhost:8000/healthz | Process is up |
| Readiness | http://localhost:8000/readyz | Dependencies are reachable |
| Temporal UI | http://localhost:8233 | Workflow history. A run's workflow is `provision-{run_id}` |
| MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| **Mailpit** | http://localhost:8025 | **Every email the system sends lands here** |
| PostgreSQL | `localhost:55432` | `receptionist` / `receptionist` |
| Redis | `localhost:56379` | |

Ports are non-default on purpose. A native PostgreSQL install already holding
5432 produces a confusing failure — Docker still reports the port as published,
but connections reach the other server and fail on authentication.

---

## 3. Running everything in containers

The default profile is infrastructure only, because the usual loop wants a
reloader and a debugger attached. To run the application containers too:

```bash
docker compose --profile app up -d --build
```

That builds the same images CI builds and production would deploy.

---

## 4. Tests

```bash
cd backend
uv run pytest                 # unit + integration, no network, no credentials
uv run ruff check .
uv run ruff format --check .
uv run mypy .

cd ../frontend
npm run lint && npm run typecheck && npm test && npm run build
```

**If you see a large number of skips**, PostgreSQL is not reachable:

```
SKIPPED [168] PostgreSQL is not reachable at postgresql+asyncpg://...
```

That is not a passing suite — it is 43% of the backend untested. Start the
stack (`docker compose up -d db`) and run it again. CI treats these skips as a
hard failure for exactly this reason.

### Testing without Redis

Correctness must never depend on the cache. To prove it, run the suite with
Redis forced off — every read takes its PostgreSQL fallback and the result must
be identical:

```bash
REDIS_ENABLED=false uv run pytest
```

---

## 5. Common operations

```bash
# Logs
docker compose logs -f api worker
docker compose logs -f temporal

# Reset the database, keep the containers
cd backend && uv run alembic downgrade base && uv run alembic upgrade head

# Destroy everything including data, and start clean
docker compose down -v && docker compose up -d
cd backend && uv run alembic upgrade head

# New migration after a model change
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head

# Confirm models and migrations agree (CI runs this)
uv run alembic check
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `password authentication failed` on 55432 | A native PostgreSQL holds the port | Change `POSTGRES_PORT` in `.env`, `docker compose up -d` |
| `alembic upgrade` hangs | Container not healthy yet | `docker compose ps`, wait for `healthy` |
| Temporal unhealthy on first boot | Schema setup takes ~40s | Wait, then `docker compose logs temporal` |
| MinIO init container `Exited (0)` | Expected — it created the bucket and stopped | Nothing to do |
| No emails anywhere | Looking in a real inbox | Mailpit at http://localhost:8025 |
| 168 tests skipped | PostgreSQL unreachable | §4 |
| Redis errors but requests still work | Working as designed | Confirm with `REDIS_ENABLED=false` |
| Provisioning stops at "Confirm your subscription" | The money gate is doing its job — the tenant has no entitlement | Expected with `TRIAL_DAYS=0`; otherwise check the `subscriptions` row |
| A run sits in `billing_blocked` | Parked, not failed. It resumes on a Stripe webhook, or after `BILLING_RECHECK_INTERVAL_S` | Nothing to do; it is not an error state |
| Provisioning never starts | The Temporal worker is not running, or was down at signup | Start `python -m app.temporal.worker`; then retry the run from the admin API |
| A run stays in `draft` forever | Its workflow was never started (Temporal was down when the signup landed) | Admin retry restarts it. There is no automatic reconciler yet |
| Two workers seem to fight over a run | Both orchestrators enabled | Only one is authoritative: check `ORCHESTRATOR`. The polling worker skips provisioning under `temporal` |

---

## 6. Using real vendors

`DRY_RUN=true` is the master switch. While it is on, Twilio, ElevenLabs, email,
Stripe and object storage resolve to fakes **regardless of what the
`*_PROVIDER` settings say**. There is no code path that spends money during a
dry run, because the decision is made once in `providers/registry.py` rather
than branched on in each step.

Turning it off is a deliberate act with real consequences:

```bash
DRY_RUN=false            # ← real vendors from here on
TWILIO_PROVIDER=twilio   # purchases cost money, monthly, until released
```

Before you do:

1. **`BILLING_GATE_ENABLED=true`.** With the gate off and `DRY_RUN=false`, every
   signup buys a real phone number. This was the POC's largest commercial leak,
   closed in M10 — a staging or production process refuses to boot with the gate
   disabled, so it can only ever be off locally.
2. **Use test credentials.** Stripe `sk_test_`, a Twilio trial subaccount.
3. **The automated suite still uses fakes.** Real-vendor testing is an explicit,
   separate command — never something the default `pytest` run can reach.
4. **Watch `phone_numbers`.** Every `ACTIVE` row is a recurring monthly charge.
   `released_at` is the evidence a number was handed back.

### Temporal, locally

The compose stack runs a real Temporal server; no Cloud account is needed. The
UI at http://localhost:8233 shows every provisioning execution, and a run's
workflow id is `provision-{run_id}` — the same UUID as the `provisioning_runs`
row, so no lookup table is required to correlate them.

The automated tests do not use the compose server at all. They run the SDK's
time-skipping environment in-process, so a ten-minute retry ladder completes
instantly and the suite needs no running Temporal.

Temporal Cloud is a configuration change, not a code change:

```bash
TEMPORAL_ADDRESS=<namespace>.<account>.tmprl.cloud:7233
TEMPORAL_NAMESPACE=<namespace>
TEMPORAL_TLS_ENABLED=true
TEMPORAL_CLIENT_CERT_PATH=/path/to/client.pem
TEMPORAL_CLIENT_KEY_PATH=/path/to/client.key
```

That path is written and type-checked but **has never been run against Cloud** —
see the M4 limitations in the migration plan.

### Stripe, locally

`PAYMENT_PROVIDER=fake` and `DRY_RUN=true` mean the whole billing path —
checkout, subscription lifecycle, webhook verification, the money gate — runs
with no Stripe account and no possibility of a charge. The fake signs and
verifies webhooks exactly as the real adapter does, so a forged delivery is
genuinely rejected rather than waved through.

To exercise real Stripe test mode:

```bash
PAYMENT_PROVIDER=stripe
STRIPE_SECRET_KEY=sk_test_...          # test key only, never sk_live_
STRIPE_WEBHOOK_SECRET=whsec_...        # from `stripe listen`
STRIPE_PRICE_PLANS=price_abc:pro,price_def:enterprise

stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
```

`DRY_RUN=true` still forces the fake, so turning Stripe on is two deliberate
changes, not one.

### Credential safety

Real credentials may live in your local `.env`. They are never printed, logged,
committed, returned through an API, or copied into documentation. The frontend
never receives any of them — `NEXT_PUBLIC_*` variables are compiled into the
browser bundle and are public by definition.

CI enforces the first half of that: a tracked `.env` fails the build, as does a
credential-shaped literal in source.
