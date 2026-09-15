# AI Receptionist

Replaces a Make.com automation chain with an observable, retryable application.
A business submits a form and, about two minutes later, has a dedicated phone
number answered by an AI receptionist configured for that business — with every
step visible and individually retryable.

```
signup → validate → generate config (LLM) → buy number (Twilio)
       → create agent (ElevenLabs) → link → verify → ACTIVE
       → inbound call answered → post-call webhook → transcript
       → summary (LLM) → email + dashboard
```

The system this replaces failed silently: a multi-step process with irreversible
external side effects, no durable state, no idempotency and no visibility. Every
design decision below exists to fix that *class* of problem, not just to move it
to Python.

> **Status: production migration M0–M20 complete.** Redis, the production
> telephony path, shared vertical agents, config versioning and rollback,
> webhook hardening, usage metering, retention, a durable notification outbox,
> the customer dashboard, the operator console, metrics, circuit breakers, a
> security suite and a full local end-to-end journey are all built and
> validated — **799 backend tests, 53 frontend tests, zero skips**.
>
> Every vendor is a fake in the test suite, which is what makes that run
> repeatable and free. See [what it does not
> prove](docs/E2E_TESTING.md#what-it-does-not-prove) before reading the number
> as production readiness. Per-module detail is in
> [`docs/PRODUCTION_MIGRATION_PLAN.md`](docs/PRODUCTION_MIGRATION_PLAN.md).

---

## 1. Purpose

| Flow | What happens |
|---|---|
| **Signup** | A business owner fills in a form. The API validates, persists and returns in milliseconds. No provider is called, so a Twilio outage cannot stop a signup. |
| **Provisioning** | A background worker walks a seven-step state machine, buying a number and creating a voice agent. Every step is durable, retryable and individually visible. |
| **Inbound call** | Twilio routes the call to an ElevenLabs agent that answers with that business's own greeting, services and hours. |
| **Post-call** | ElevenLabs posts a transcript. The call is stored, an LLM summarises it, and the owner gets an email. |

---

## 2. Architecture

```
Next.js  ──POST /api/v1/signups──►  FastAPI  ──►  PostgreSQL
   ▲                                   │              ▲
   │ polls status                      │              │ the source of truth
   └───────────────────────────────────┘              │
                                                      │
                    Worker (polling loop) ────────────┘
                                │
      ┌────────────┬────────────┼─────────────┬──────────────┐
      ▼            ▼            ▼             ▼              ▼
  LLM (config)  Twilio     ElevenLabs      Email        LLM (summary)

Inbound call ──► Twilio ──► ElevenLabs agent ──► post-call webhook ──► FastAPI
```

### Ground rules

1. **PostgreSQL is the source of truth.** No provisioning state lives in memory.
   External providers hold *projections* of what the database says.
2. **Deterministic code handles money, infrastructure and state transitions.**
   LLMs handle language only — never number selection, retries, activation or
   any infrastructure decision.
3. **Every external side effect is idempotent.** A worker restart cannot
   double-buy a number, duplicate an agent, or resend an email.
4. **Every vendor sits behind a `Protocol` with a fake**, so the whole
   provisioning path runs in CI with no network.
5. **`DRY_RUN=true` is the default**, and it forces fakes for everything that
   spends money.

### Deliberately excluded from the POC

Temporal · Redis · Stripe · Kubernetes · microservices · RBAC · a custom
STT→LLM→TTS media pipeline. See §17 for what production would add.

---

## 3. Directory structure

```
backend/
  app/
    asgi.py                 ASGI entrypoint (reads the environment)
    main.py                 create_app() factory, middleware, error envelope
    worker.py               the polling worker (python -m app.worker)
    api/
      rate_limit.py         in-process sliding window for signups
      health.py             /healthz, /readyz
      v1/                   signups, tenants, webhooks, admin
    core/                   config, logging, correlation ids, error taxonomy
    data/area_codes.py      area code → state → timezone (no LLM involved)
    db/                     declarative base, session, repository base
    models/                 9 SQLAlchemy models + the state machine enums
    prompts/                versioned prompt templates (*.md)
    providers/
      protocols.py          LLMProvider, TwilioProvider, ElevenLabsProvider, EmailProvider
      models.py             normalized provider DTOs
      registry.py           the one place that picks fake vs real
      fakes/                in-memory vendors with failure injection
      real/                 httpx adapters for Twilio, ElevenLabs, Anthropic/OpenAI, Resend/SendGrid
    provisioning/
      engine.py             the state machine: one step per call
      registry.py           step name → implementation
      retry.py              backoff and retryable/terminal classification
      compensation.py       release the number, delete the agent
      steps/                one module per step
    schemas/                Pydantic contracts (signup, views, agent config, business)
    services/               signup, config generation, summarization, webhooks, notifications
  migrations/               Alembic
  tests/                    340 tests
frontend/
  app/                      Next.js App Router: signup form + status page
  lib/                      typed API client
  tests/                    19 tests (vitest + testing-library)
docs/                       architecture decisions and the POC plan
```

---

## 4. Backend setup

Requires **Python 3.12** and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # then edit; never commit .env
cd backend
uv sync
```

## 5. Frontend setup

Requires **Node 20+**.

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local
```

## 6. PostgreSQL setup

```bash
docker compose up -d db       # PostgreSQL 16 on port 55432
```

> **Why 55432 and not 5432?** A native PostgreSQL install commonly already holds
> 5432 (and 5433 if it runs two clusters). The clash is silent: Docker still
> reports the port as published, but connections reach the *other* server and
> fail with a confusing `password authentication failed`. Change `POSTGRES_PORT`
> in `.env` if 55432 is also taken.

A hosted database works too — set `DATABASE_URL` to a Neon or Supabase URL. It
must use the `postgresql+asyncpg://` driver; a sync URL is rejected at startup.

## 7. Environment variables

`.env.example` is the complete list, with comments. The ones that matter most:

| Variable | Default | Why it matters |
|---|---|---|
| `DRY_RUN` | `true` | Forces fake Twilio, ElevenLabs and email. **Nothing can spend money while this is true.** |
| `DATABASE_URL` | local | Must be `postgresql+asyncpg://`. |
| `LLM_PROVIDER` | `fake` | `fake` \| `anthropic` \| `openai`. Not forced by `DRY_RUN` — running a dry run against a real model is often the point. |
| `TWILIO_PROVIDER` | `fake` | `fake` \| `twilio` |
| `ELEVENLABS_PROVIDER` | `fake` | `fake` \| `elevenlabs` |
| `EMAIL_PROVIDER` | `fake` | `fake` \| `resend` \| `sendgrid` |
| `ELEVENLABS_WEBHOOK_SECRET` | empty | Without it, deliveries are recorded but flagged unverified, and refused outright in staging/production. |
| `ADMIN_API_KEY` | empty | Gates `/api/v1/admin/*`. When unset the admin routes are open in local/test and **refused** in staging/production. |
| `ELEVENLABS_VOICE_MAP` | 3 voices | Greeting style → voice. Deterministic; an LLM never picks a voice. |
| `PUBLIC_API_URL` | empty | Set this when the API sits behind a tunnel or proxy. Twilio signs the *public* URL it called, so without it every Twilio signature check fails. |

Selecting a real provider without its credential **fails at startup**, not
halfway through a tenant's provisioning.

`NEXT_PUBLIC_*` variables are compiled into the browser bundle and are public by
definition. Never put a provider key in one.

## 8. Alembic migrations

```bash
cd backend
uv run alembic upgrade head                          # apply
uv run alembic revision --autogenerate -m "add x"    # generate, then REVIEW
uv run alembic downgrade -1                          # step back
uv run alembic current                               # where am I
```

The URL comes from `DATABASE_URL`, never from `alembic.ini`, so no credential is
written to a committed file. Always read a generated migration before applying
it — autogenerate is a first draft. It does not emit CHECK constraints, and it
will happily produce a `NOT NULL` column addition that fails on a populated
table; both have already bitten this schema once and are noted in the migrations
that fixed them.

## 9–11. Running it

Four processes since M4 moved orchestration to Temporal. Start the
infrastructure first — `docker compose up -d` brings up PostgreSQL, Redis,
Temporal, MinIO and Mailpit; `docker compose up -d db` is enough for the POC
path alone.

See [`docs/LOCAL_PRODUCTION_SETUP.md`](docs/LOCAL_PRODUCTION_SETUP.md) for the
full local stack, including the Temporal UI and Mailpit.

```bash
docker compose up -d

# terminal 1 — API
cd backend && uv run alembic upgrade head && uv run uvicorn app.asgi:app --reload --port 8000

# terminal 2 — Temporal worker. This is what drives provisioning.
cd backend && uv run python -m app.temporal.worker

# terminal 3 — polling worker. Post-call processing, the notification outbox,
# and the retention and usage-rollup maintenance pass. Under
# ORCHESTRATOR=temporal it deliberately does not claim provisioning runs.
cd backend && uv run python -m app.worker

# terminal 4 — frontend
cd frontend && npm run dev
```

Then open <http://localhost:3000>.

- <http://localhost:8000/healthz> — liveness (never touches the database)
- <http://localhost:8000/readyz> — readiness (a real `SELECT 1`)
- <http://localhost:8000/docs> — OpenAPI (hidden in staging/production)

## 12. DRY_RUN mode

With `DRY_RUN=true` the entire flow runs end to end and nothing is bought, no
agent is created and no email is delivered. The fakes are not stubs: they keep
state, enforce the same uniqueness the real vendors do, reject a number that is
already sold, and can be told to fail — so the retry, adoption and compensation
paths are genuinely exercised rather than skipped.

`DRY_RUN` forces fakes for Twilio, ElevenLabs and email *regardless* of the
`*_PROVIDER` settings, so there is no configuration that quietly spends money.

## 13. Using the real providers

Set `DRY_RUN=false` and switch the provider you want, one at a time.

```env
DRY_RUN=false
TWILIO_PROVIDER=twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
```

**Twilio.** Start with a trial account. Every successful run buys a real number
at roughly $1.15/month, forever, until something releases it. The compensation
path and the admin *abandon* action exist for exactly this; there is no billing
gate in the POC (see §16).

**ElevenLabs.** `ELEVENLABS_PROVIDER=elevenlabs` plus `ELEVENLABS_API_KEY`. Set
`ELEVENLABS_WEBHOOK_SECRET` to the signing secret from the webhook settings, and
point the post-call webhook at
`https://<your-host>/api/v1/webhooks/elevenlabs/post-call`. For local testing,
tunnel with ngrok or similar — and set `PUBLIC_API_URL` to the tunnel's address,
or Twilio's signature check will compare against the wrong URL and always fail.

**LLM.** `LLM_PROVIDER=anthropic` plus `ANTHROPIC_API_KEY` (or the OpenAI pair).

**Email.** `EMAIL_PROVIDER=resend` plus `RESEND_API_KEY`, and set `EMAIL_FROM` to
a verified sender.

> The real adapters have **not been exercised against the live APIs** — see §16.

## 14. Testing

```bash
# backend
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests migrations
uv run pytest

# frontend
cd frontend
npm run lint && npm run typecheck && npm run test && npm run build
```

Backend tests run against **real PostgreSQL**, never SQLite: the schema uses
JSONB, partial unique indexes and `FOR UPDATE SKIP LOCKED`, and a SQLite
stand-in would pass while proving nothing. The scratch database is built by
running **the migrations**, so a model changed without a migration fails in CI
rather than in production. With no database reachable the database tests skip
and say why.

## 15. The end-to-end POC flow

`backend/tests/test_end_to_end.py` walks the whole thing with fakes. To do it by
hand with all three processes running:

1. Fill in the form at <http://localhost:3000> and submit.
2. The status page appears and polls. All seven steps are listed from the first
   render, because the backend writes them at signup.
3. Within a few seconds the run reaches ACTIVE and the number is shown.
4. Simulate an inbound call by posting a transcript:

   ```bash
   curl -X POST http://localhost:8000/api/v1/webhooks/elevenlabs/post-call \
     -H 'content-type: application/json' \
     -d '{"type":"post_call_transcription","data":{
           "conversation_id":"conv_demo_1",
           "metadata":{"call_duration_secs":74,
             "phone_call":{"agent_number":"<the number from step 3>",
                           "external_number":"+15559998888"}},
           "transcript":[{"role":"user","message":"Anything Thursday morning?"}]}}'
   ```

5. The worker summarises the call and "sends" the email. Both appear on the
   status page and in the worker's log.
6. `GET /api/v1/admin/runs` lists every run with its status and last error.

---

## 16. Known limitations

**Verified by tests, running against real PostgreSQL with fake vendors:**
signup and validation, the full provisioning state machine, retry and backoff,
terminal-vs-retryable classification, crash recovery, concurrent workers,
compensation, the Twilio fallback ladder, config generation with its repair
retry and deterministic fallback, webhook signature verification and dedupe,
call summarization and its failure paths, the admin actions, and the frontend
form and status page. 340 backend tests and 19 frontend tests.

**Implemented but never run against the live vendor APIs.** The `real/` adapters
for Twilio, ElevenLabs, Anthropic, OpenAI, Resend and SendGrid are written from
each vendor's documented interface. **This is still the single largest gap.**

What *is* now covered, by `tests/test_real_adapters.py`: each adapter is driven
through an `httpx.MockTransport`, so the request it builds (method, path, query,
auth header, body encoding) and its parsing of a representative response are
asserted without a network. That removes the "we misremembered a field name and
it crashes on first contact" class of failure. It does **not** confirm that the
endpoint paths or payload shapes match what the vendors actually accept — only a
live account can do that.

**No inbound call has actually been placed.** The voice path depends on
ElevenLabs' native Twilio import, which cannot be tested without both live
accounts and a real phone.

**No billing gate.** Every accepted signup will buy a real number once
`DRY_RUN=false`. There is compensation and an admin release action, but nothing
stops an anonymous form submission from costing money. This is the highest-value
remaining fix and it is a business decision, not a technical one.

**No authentication.** The status page treats a tenant's UUID as an unguessable
capability. Anyone with the id can read that tenant's calls. Fine for a POC,
not for customers.

**Admin protection is a shared secret**, not an auth system. It fails closed in
production-like environments, which is the most that can be said for it.

**Timezones are approximate for split states.** `area_codes.py` records the
predominant zone per state, so a business in the Florida panhandle or western
Kentucky is given Eastern when it is Central. The value is stored on the tenant
and can be corrected; the form should confirm it.

**Opening-hours parsing declines more than it accepts.** Anything outside the
common patterns returns `None` and is handed to the LLM. That is deliberate:
telling a caller a shop is open when it is shut is worse than not knowing.

**One ElevenLabs agent per tenant** is known technical debt
(`docs/00_DECISIONS.md` §2). Prompt improvements must be re-pushed per tenant.
`resync_agent` exists so that is a fan-out rather than an archaeology exercise.

**Rate limiting is per process.** In-memory, so it protects a single-replica
POC and nothing more.

**No CI.** The validation commands exist; nothing enforces them on push.

**Transcripts are stored** as text plus turns, in `calls.transcript_json`. They
are never returned by any API and never logged, but they are not encrypted at
rest and there is no retention policy.

## 17. Production roadmap

Ordered by value, largely following `docs/02_PLAN_PRODUCTION.md`:

1. **Verify every real adapter against a live account**, then keep contract
   tests recorded against them.
2. **A billing gate before provisioning** (Stripe), plus a nightly reaper that
   releases numbers for abandoned tenants. Stops the money leak.
3. **Real authentication** — magic-link login, so a tenant id stops being a
   capability.
4. **Temporal** in place of the polling engine. Each step is already a pure
   `(context) -> result` function, so the migration is mechanical.
5. **Redis** for webhook dedupe fast-path, distributed locks, rate limiting and
   the hot config lookup — with PostgreSQL still the source of truth.
6. **A shared agent per vertical** with per-call dynamic variables, so a prompt
   fix ships once instead of N times.
7. **Own the media path** (`<Connect><Stream>`), for call records and routing
   independent of the vendor.
8. **PII handling** — encryption at rest, a retention policy, a delete-my-data
   path, and recording-consent announcements where state law requires them.
9. **Observability** — OpenTelemetry traces keyed by tenant and call, SLOs, and
   alerting that pages someone when a run fails.

---

## Documents

| File | Purpose |
|---|---|
| [`docs/00_DECISIONS.md`](docs/00_DECISIONS.md) | Architecture decisions and rationale |
| [`docs/01_PLAN_POC.md`](docs/01_PLAN_POC.md) | Plan A — the POC. The build spec. |
| [`docs/02_PLAN_PRODUCTION.md`](docs/02_PLAN_PRODUCTION.md) | Plan B — production hardening |
| [`docs/PRODUCTION_MIGRATION_PLAN.md`](docs/PRODUCTION_MIGRATION_PLAN.md) | **M0–M20, what each module did and why** |
| [`docs/LOCAL_PRODUCTION_SETUP.md`](docs/LOCAL_PRODUCTION_SETUP.md) | Running the full local stack |
| [`docs/TELEPHONY.md`](docs/TELEPHONY.md) | Both call paths, and what a caller hears when things break |
| [`docs/WEBHOOKS.md`](docs/WEBHOOKS.md) | Webhook setup, signatures, replay protection |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Incident response, recovery, known gaps |
| [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md) | The full journey, and what it does not prove |
| [`backend/README.md`](backend/README.md) | Backend conventions and workflow |
