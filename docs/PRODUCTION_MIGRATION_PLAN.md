# Production Migration Plan — POC → Production Architecture v1

**Status:** M0–M20 complete and validated against the running local stack.
**Audit date:** 2026-09-09 · **Completed:** 2026-09-13
**Scope:** evolve the existing POC in place. Not a rewrite.

This document is the output of an actual inspection of the repository, not a
restatement of `docs/02_PLAN_PRODUCTION.md`. Where that document describes the
*intent*, this one records the *measured current state*, the gap, and the order
in which the gap gets closed.

---

## 0. Executive summary

The POC is in better shape than a POC usually is. ~8,950 lines of backend
Python, strict mypy clean, ruff clean, 393 tests. The disciplines that are
expensive to retrofit — typed provider interfaces with fakes, a single
repository layer, durable provisioning state, idempotency guards before every
paid vendor call, partial unique indexes protecting money-spending invariants —
are **already present**. That is the reason this is an evolution and not a
rewrite.

The gap to production is therefore *not* code quality. It is five missing
capabilities and one missing environment:

| # | Gap | Consequence today |
|---|---|---|
| 1 | ~~No authentication or authorization at all~~ | **CLOSED in M3.** Was: anyone who knew a tenant UUID could read that tenant's calls and transcripts |
| 2 | ~~No billing gate~~ | **CLOSED in M10.** Was: every anonymous form submit bought a real Twilio number — unbounded spend |
| 3 | ~~Orchestration is a polling worker~~ | **CLOSED in M4.** Durable Temporal workflows; the polling loop no longer claims provisioning runs |
| 4 | No Redis | The production call path (`/voice/init`, 300ms budget) cannot be built on Postgres alone |
| 5 | No production call path | Calls use ElevenLabs' native Twilio import; we do not own routing, fallback, or call lifecycle |
| 6 | ~~No container runtime on this machine~~ | **RESOLVED.** Docker Desktop + WSL2 installed 2026-09-09; the full stack runs and all 168 previously-skipped tests now pass. |

Items 1, 2, 3 and 6 are closed; §1 and §5 record how. The largest remaining
risk is call reliability: the production call path (M6) and the Redis layer it
needs (M5). Neither leaks money or data today, but a caller hearing silence is
the most customer-visible failure left.

---

## 1. Environment — resolved

The original audit found no container runtime on the development machine: no
Docker Desktop, no WSL2, no podman, and no local PostgreSQL. The measured
consequence was that **168 of 393 backend tests skipped** on "PostgreSQL is not
reachable" — 43% of the backend untested behind a green-looking summary line.

**Resolved 2026-09-09.** Docker Desktop 4.89 (a per-user install, so the CLI is
under `%LOCALAPPDATA%\Programs\DockerDesktop`, not `Program Files`) plus WSL2
with the Virtual Machine Platform feature enabled. The full stack now runs:

```
docker compose ps
  ai-receptionist-db            healthy   55432
  ai-receptionist-redis         healthy   56379
  ai-receptionist-temporal      healthy   7233
  ai-receptionist-temporal-ui   up        8233
  ai-receptionist-minio         healthy   9000/9001
  ai-receptionist-mailpit       healthy   1025/8025
```

Every previously-skipped test now runs. Current state:

```
backend:   484 passed, 0 skipped
ruff:      All checks passed        ruff format: 133 files formatted
mypy:      Success, 130 source files (strict)
alembic:   single head; check clean; downgrade/upgrade round trip verified
frontend:  lint clean, typecheck clean, 28 tests, build clean
```

**A green summary line is not a passing suite — check the skip count.** CI
(`.github/workflows/ci.yml`) fails the build if the database tests skip, so this
particular blind spot cannot silently return.

---

## 2. Current architecture (measured)

```
frontend/  Next.js 16 + React 19 + Tailwind 4      (4 test files, 25 tests)
  app/page.tsx, SignupForm.tsx
  app/status/[tenantId]/           <- public status page, keyed by raw UUID

backend/   FastAPI + SQLAlchemy 2 async + Alembic  (~8,950 LOC)
  api/        health, v1/{signups, tenants, admin, webhooks}   14 routes total
  core/       config (typed Settings, SecretStr), logging, errors,
              correlation, readiness
  db/         base (naming conventions, UUID PK + timestamp mixins),
              session (engine, per-request UoW), repository
  models/     tenant, business_profile, agent_config, agent, phone_number,
              call, provisioning{_runs,_steps}, webhook_event, enums
  providers/  protocols.py  <- LLM / Twilio / ElevenLabs / Email Protocols
              real/{llm,telephony,voice,mail}.py
              fakes/{llm,telephony,voice,mail}.py
              registry.py, http.py, models.py
  provisioning/ engine.py (398 LOC state machine), retry, compensation,
              context, registry, steps/{validate, generate_config,
              purchase_number, create_agent, link_number, verify, activate}
  services/   signup, config_generator, prompt_renderer, normalization,
              summarizer, call_processor, webhooks, signatures,
              notifications, email_templates, idempotency, tally
  worker.py   polling loop: SELECT ... FOR UPDATE SKIP LOCKED

migrations/ 3 revisions, single head
docker-compose.yml   postgres only
```

### What is already production-shaped

Do not rebuild these. They satisfy the brief as written:

- **Provider abstraction (§42).** `providers/protocols.py` defines runtime-
  checkable Protocols; every vendor has a real and a fake implementation;
  business logic imports no vendor SDK. `DRY_RUN=true` forces fakes for Twilio,
  ElevenLabs and email via `Settings.effective_*_provider`, so a dry run
  cannot spend money. This is exactly the structure §42 and the closing note of
  the brief ask for, already built.
- **Typed configuration (§5).** `core/config.py` — Pydantic Settings, `SecretStr`
  for every credential, startup validation that a real provider has its
  credential, async-driver enforcement on `DATABASE_URL`.
- **Durable provisioning state (§6).** `provisioning_runs` / `provisioning_steps`
  with `next_attempt_at`-driven backoff that survives restart, step timeout for
  orphan recovery, and explicit compensation (`compensation.py` releases a
  purchased number when a later step fails).
- **Money-safety invariants enforced by the database, not by code.** Partial
  unique indexes: one live number per tenant, one live e164, one live agent per
  tenant, one live config per tenant, one live signup per contact email. Check
  constraints tying `twilio_sid` to purchased state and `released_at` to
  released state. Adoption guards (`find_by_friendly_name`, `find_agent_by_name`)
  called before every purchase so a crashed retry adopts rather than re-buys.
- **Immutable, versioned agent configs (§37).** `agent_configs` is append-only
  with `version`, `template_version`, `generated_by`, and an `is_live` flag —
  rollback is a flag move, no vendor call. ElevenLabs is already a projection,
  not the source of truth.
- **Webhook durability (§16), partially.** `webhook_events` table with
  RECEIVED/PROCESSED/DUPLICATE/REJECTED/FAILED, `signatures.py` for verification,
  unique `provider_call_id` on `calls` as the replay defence.
- **Correlation IDs, structured logging, `/healthz` + `/readyz` split.**

### What does not exist

Verified absent by inspection, not assumed:

| Area | Finding |
|---|---|
| Auth | No users, sessions, memberships, roles, or login. `GET /tenants/{id}` is unauthenticated; the raw tenant UUID *is* the credential. Admin routes use a single shared `ADMIN_API_KEY`, open by default in local/test. |
| Billing | No Stripe, no subscriptions, no plans table, no trial eligibility, no gate. `TenantPlan` exists as an enum only. §10's "biggest commercial leak" is fully open. |
| Redis | Absent. `services/idempotency.py` and `api/rate_limit.py` exist but are Postgres/in-process. |
| Temporal | Absent. Orchestration is `worker.py` + `provisioning/engine.py`. |
| Production call path | Absent. No `/voice/inbound`, no `/voice/init`, no TwiML. Calls arrive only via the ElevenLabs post-call webhook. |
| Shared vertical agents | Absent. One agent per tenant; `agent_mode` flag does not exist. |
| Usage / cost metering | Absent. No `usage_events`, no aggregates, no reconciliation. |
| Object storage | Absent. No recordings stored anywhere. |
| PII controls | Transcripts stored as plaintext JSONB. No encryption abstraction, no retention, no delete-my-data. |
| Audit log | Absent. |
| OpenTelemetry | Absent. |
| CI | No `.github/workflows`. |
| Frontend | Signup form + public status page only. No dashboard, no auth, no calls view, no billing, no admin. |

---

## 3. Target architecture

Unchanged from the brief; restated here as the contract every module is
measured against.

```
Next.js SaaS frontend
        |
    FastAPI API  ──────────────┐
        |                      |
  PostgreSQL              Redis
  SOURCE OF TRUTH         cache / locks / rate limit / dedupe
        |                 (never authoritative)
    Temporal
    workflows
        |
  ┌─────┼──────┬─────────┬────────┐
Twilio  ElevenLabs  LLM   Stripe  Email
        |
      MinIO (recordings, exports)
```

**Invariants that no module may violate:**

1. PostgreSQL is the source of truth. Redis loss degrades latency, never
   correctness. Every Redis read has a Postgres fallback.
2. Temporal is orchestration, not a database. `provisioning_runs` /
   `provisioning_steps` remain the read model the dashboard and admin API query.
3. No vendor is the source of truth for application state — not ElevenLabs for
   config, not Twilio for call state, not Stripe for entitlement.
4. The LLM never decides infrastructure, money, retries, state, or authorization.
5. No Twilio number is purchased without an explicit billing or trial grant.

### Local → AWS mapping

One architecture, two infrastructure bindings. Only adapters and configuration
change.

| Component | Local now | AWS later |
|---|---|---|
| PostgreSQL | Docker `postgres:16` | RDS |
| Redis | Docker `redis:7` | ElastiCache |
| Temporal | Docker `temporalio/auto-setup` | Temporal Cloud |
| Object storage | MinIO behind `ObjectStorageProvider` | S3 (same interface) |
| Email | Mailpit behind `EmailProvider` | Resend / SendGrid (adapters exist) |
| Stripe | Test mode + Stripe CLI | Live mode |
| API | uvicorn / Docker | ECS |
| Frontend | `next dev` / Docker | AWS hosting |
| Worker | Temporal worker process | ECS |
| Secrets | `.env`, gitignored | Secrets Manager / Doppler |
| Observability | OTel + local collector | AWS + Grafana / Sentry |

---

## 4. Module order and dependencies

Ordered by risk, not by visibility, per the brief's closing guidance. The
frontend is deliberately late: the production risks are money leakage, tenant
isolation, call reliability and silent provisioning failure — none of which live
in the UI.

```
M0  audit + this plan                       ← COMPLETE
M1  config / environment redesign           ← COMPLETE
M2  database, schema, tenant isolation      ← COMPLETE
M3  authentication + authorization          ← COMPLETE
M4  Temporal orchestration                  ← COMPLETE
M5  Redis infrastructure                    ← COMPLETE
M6  production telephony path               ← COMPLETE
M7  shared ElevenLabs agent architecture    ← COMPLETE
M8  LLM config generation + versioning      ← COMPLETE
M9  webhook security + replay               ← COMPLETE
M10 billing / Stripe                        ← COMPLETE
M11 usage + cost metering                   ← COMPLETE
M12 PII, encryption, retention              ← COMPLETE
M13 notifications / email                   ← COMPLETE
M14 customer dashboard                      ← COMPLETE
M15 admin panel                             ← COMPLETE
M16 observability                           ← COMPLETE
M17 outage handling                         ← COMPLETE
M18 load + security testing                 ← COMPLETE
M19 CI/CD readiness                         ← COMPLETE
M20 full local production E2E               ← COMPLETE
```

All modules are validated against the real stack: **799 backend tests, 53
frontend tests**, ruff, strict mypy, `alembic check`, a reversible migration
round trip, and a production frontend build. Every vendor is a fake — see
"Limitations" below for exactly what that does and does not prove.

---

## 5. Per-module scope

### M1 — Configuration and environment
Split `.env.example` into `.env.example` / `.env.local.example` / `.env.test.example`.
Add typed settings for Redis, Temporal, object storage, Stripe, OTel, auth,
Mailpit. Keep everything centralized in `core/config.py`; add per-environment
validation (production-like environments must reject blank admin key, blank
webhook secrets, `DEBUG=true`). No invented variables beyond what a module reads.

### M2 — Database and tenant isolation
New tables: `users`, `memberships`, `sessions`, `audit_logs`, `subscriptions`,
`billing_events`, `usage_events`, `usage_daily`, `idempotency_keys`,
`notifications`, `notification_attempts`, `recordings`, `data_deletion_requests`.
Reuse rather than duplicate: `tenants` already serves as the organization;
`agent_configs` already serves as config versions; `webhook_events` already
exists. Expand/contract migrations only. Centralize tenant scoping in the
repository layer and add tests that *attempt* cross-tenant reads and must fail.

### M3 — Auth (COMPLETE)
Two ways in, one session cookie (HttpOnly, Secure, SameSite=lax). Sessions and
magic links share one table because they are one object at two ages; only
SHA-256 hashes are stored and links are single-use via `consumed_at`. Roles
owner/admin/member with an explicit rank map — comparing `StrEnum` members
compares strings, which would make "admin" outrank "owner" alphabetically.

Passwords were added after the magic-link-only design proved unshippable: a
link cannot be delivered before outbound email is configured, so a business
that had just signed up held an account it could not open. `users.password_hash`
is Argon2id and nullable — an account that never chose one still signs in by
link. Signup creates the owner account and its membership in the same
transaction as the tenant, and never writes a password onto an address that
already has an account, which would be a takeover by anyone who knew the email.
Password reset is not built and needs working email; the magic link is the
interim recovery path.

Enforcement is a FastAPI dependency (`app/api/auth_deps.py`) rather than a check
inside handlers, so a new endpoint cannot forget it. A tenant the caller cannot
see returns **404, not 403** — a 403 confirms existence and turns any protected
endpoint into an enumeration oracle. Platform admins do not bypass membership;
they impersonate, which is audited distinctly.

"Raw tenant UUID as credential" is retired. The post-signup status page, which
has no logged-in user, now carries a signed, expiring, tenant-scoped grant
(`app/services/status_tokens.py`) issued by the signup response. Login is rate
limited on address+source — address alone would let an attacker lock a customer
out of their own account.

55 auth tests, including: replayed link, expired link, session token presented
as a link, revoked session, disabled user, unaccepted invitation, forged and
edited grants, and the enumeration-oracle checks.

### M4 — Temporal
`ProvisionTenantWorkflow` with the seven existing steps as activities.
The existing step implementations are reused as activity bodies; their
idempotency guards are what make them safe to retry. Saga compensation stays
explicit. `provisioning_runs`/`provisioning_steps` are projected from workflow
events and remain what the API queries. Workflow ID = tenant ID for
single-flight. The polling worker is kept behind a flag until Temporal
equivalents pass their tests, then removed.

### M4 — Temporal (COMPLETE, local server only)

Durable orchestration. The polling engine's hand-rolled scheduling —
`next_attempt_at`, a lease, a backoff ladder, `claim_runs` with
`FOR UPDATE SKIP LOCKED` — is replaced by Temporal's retry policy, timers and
saga. Everything else is unchanged.

**The step implementations are reused verbatim.** An activity opens a session,
does the same three-phase bookkeeping, and calls
`app.provisioning.registry.implementation_for(step)`. The money-safety guards
from M10 and the POC — the billing gate immediately before the purchase, the
Twilio adoption guard, the durable PENDING row, per-step idempotency keys — are
the *same code*. Temporal changed when a step runs, not what it does.

**Architecture**

```
app/temporal/
  shared.py      identifiers + dataclasses; imported inside the sandbox
  activities.py  ProvisioningActivities — every side effect
  workflows.py   ProvisionTenantWorkflow — the saga
  client.py      connect / start / signal
  worker.py      python -m app.temporal.worker
```

Workflow id is `provision-{run_id}`. Keyed by run, not tenant: a tenant
legitimately has several runs over its lifetime. Single-flight per tenant is
still enforced by `uq_provisioning_runs_in_flight_per_tenant`, so the guarantee
holds even for code paths that never reach Temporal. A duplicate start raises
`WorkflowAlreadyStartedError`, which `start_provisioning` catches and treats as
adoption — so a resubmitted signup joins the running execution rather than
starting a second orchestrator.

**Retry, timeout, compensation**

- Forward steps: 5s initial, ×2, capped at 10 minutes, `max_attempts` from
  `PROVISIONING_MAX_ATTEMPTS`. `non_retryable_error_types` carries
  `billing_blocked`, `invalid_input`, `not_found`, `configuration_error`,
  `forbidden`, `unauthenticated`, `dry_run_blocked` — so a rejected signup fails
  in one attempt instead of burning the schedule.
- Errors are classified, never guessed: `AppError.retryable` becomes
  `ApplicationError(non_retryable=...)`. An *unexpected* exception stays
  retryable, capped by max attempts.
- Compensation is retried harder (10 attempts) than any forward step, because
  an unreleased number bills every month forever. It is idempotent by
  construction, so an interrupted compensation recovers by simply running again
  — which is what Temporal does.
- The saga spans `PURCHASE_NUMBER → VERIFY`. `ACTIVATE` sits deliberately
  outside it: a failed welcome email must not release a working number.

**The billing gate is a loop, not a failure.** On refusal the workflow parks the
run in `BILLING_BLOCKED` and waits on `wait_condition` for either the Stripe
signal or the re-check timer, then asks again — indefinitely. Failing into
compensation would release a number for a customer one card entry from being
entitled. The timer matters as much as the signal: a webhook that is never
delivered must not strand a paying customer.

**The polling worker.** `app/worker.py` no longer claims provisioning runs when
`ORCHESTRATOR=temporal` (now the default). That guard is a money-safety
property, not tidiness — two orchestrators over the same runs would both advance
them and could both buy a number. The polling engine is retained behind
`ORCHESTRATOR=state_machine` as a documented fallback: it is the path 500-odd
tests already prove, and keeping it runnable means a Temporal outage has an
answer that is not "provisioning is down". There is exactly one authoritative
orchestrator at a time. Post-call processing still polls either way — that is
queue work, not orchestration.

**Observability.** The workflow id is *derived* from the run id, so no column
and no migration were needed to correlate them: `provision-{run_id}` in the
Temporal UI is the `provisioning_runs` primary key. Activities log
`workflow_id` and `activity_attempt` alongside `run_id`, `tenant_id` and the
application `correlation_id`.

**Secrets.** Activity arguments are recorded verbatim in workflow history, which
is retained and queryable, so the contract is identifiers only — activities load
what they need from the database themselves. A test asserts the input dataclasses
carry nothing outside `{run_id, tenant_id, correlation_id, step}`.

**No migration.** Nothing about the schema changed.

**Validated live, not only in tests.** A signup posted to the running API
returned in milliseconds with `provisioning_status: draft` (the POC property —
the front door never calls a provider), and the Temporal worker drove it to
`active` in 2.7 seconds against the compose Temporal server. Workflow history
shows the eight steps in order, and every activity input in that history is
`{run_id, step}` — a scan of all decoded payloads found no credential patterns.
The polling worker, run for 25 seconds against a deliberately orphaned `draft`
run, logged `provisioning_owner=temporal` and left it untouched: still `draft`,
attempt 0, zero numbers purchased.

**Limitations — what is NOT proven.**

- **Tested against the local Temporal server and the SDK's time-skipping test
  environment only. Temporal Cloud has never been exercised.** The mTLS path in
  `client.py` is written and type-checked but has never connected to Cloud;
  confirming it needs a namespace and a certificate pair.
- **A run whose workflow never started sits in DRAFT.** Signup starts the
  workflow best-effort so a Temporal outage cannot break the front door, but
  there is no reconciler sweeping for runs that have no execution. Today that
  needs an operator clicking retry. A periodic reconciliation workflow is the
  obvious fix and is not built.
- The polling engine remains in the codebase behind a flag. It is a fallback,
  not an equal path, and is not exercised by the Temporal tests.
- Worker deployment is a compose service; no autoscaling, no separate task
  queues per activity type, no rate limiting of vendor-facing activities.

### M5 — Redis
Exactly the key patterns in `docs/02_PLAN_PRODUCTION.md` §3: `num:{e164}`,
`cfg:{tenant}:v{version}`, `wh:{provider}:{event_id}`, `lock:provision:{tenant}`,
`rl:*`. Explicit TTLs, single-flight on config to prevent stampede, Postgres
fallback on every path, and a test suite variant that runs with Redis
unavailable and must still pass on correctness.

### M6 — Telephony
`/voice/inbound` returning TwiML `<Connect><Stream>`; `/voice/init` serving
dynamic variables under a 300ms p99 budget via Redis → Postgres → generic safe
config. ElevenLabs failure falls back to a recorded message and voicemail. A
caller never gets a dead line.

### M10 — Billing gate (COMPLETE)

The money gate. Closes the leak `docs/02_PLAN_PRODUCTION.md` §4 calls "the
single biggest": every anonymous form submission bought a real Twilio number.

**Where the gate is.** Two enforcement points, one decision function
(`app/services/billing_gate.py`):

1. `ProvisioningStep.BILLING_GATE` — a real step in `STEP_SEQUENCE`, sitting
   immediately before `PURCHASE_NUMBER`. Gives visibility and a clean parked
   state.
2. `purchase_number.run()` — re-evaluates the *same* function twice more: once
   as Guard 0 before the adoption guards, and once after number selection,
   immediately before `twilio.purchase_number()`.

The second point is what actually guarantees money safety. A step retry
re-enters `purchase_number` without re-running the gate step; an admin
"retry from step" can start there directly; entitlement can lapse while a run is
parked for hours; and two workers can race. Only the check performed in the same
moment as the spend is authoritative.

**Blocked ≠ failed.** `BillingBlockedError` is neither retryable nor terminal.
The engine parks the run in `ProvisioningStatus.BILLING_BLOCKED` — an *in-flight*
status, so the tenant keeps owning it and no duplicate run can start — rather
than failing it. Failing would trigger saga compensation and release resources
for a customer one card entry away from being entitled. The tenant stays
`PENDING`: never `ACTIVE` (which would claim a receptionist that does not exist)
and never `FAILED` (untrue, and shows an error for something payment fixes).

**Resuming.** A Stripe webhook granting entitlement re-arms the run for the next
worker poll. It only moves `next_attempt_at` — the gate is re-evaluated from
scratch when the run is picked up, so a webhook can never *grant* entitlement,
only cause the question to be asked again. A parked run also re-checks itself on
`BILLING_RECHECK_INTERVAL_S`, so a webhook that is never delivered cannot strand
a paying customer.

**Stripe is not the source of truth.** Entitlement is read from `subscriptions`
on every check. Nothing calls Stripe to ask. A Stripe outage therefore cannot
decide that every customer is unpaid, and the provisioning path gains no third
party. Webhooks write; nothing reads back.

**Webhook security.** `POST /api/v1/webhooks/stripe` verifies the signature over
the raw body before anything is stored — unlike the other webhooks, in *every*
environment, because this endpoint is the only way billing state changes. A
rejected delivery is deliberately not persisted: storing attacker-controlled
content into a table the admin panel renders is how a webhook becomes an
injection vector. Replay is refused by the unique `(provider, event_id)` index,
and out-of-order events are dropped by a period-start comparison so a delayed
`updated` cannot resurrect a cancelled plan.

**No migration.** M2 already created `subscriptions` and `billing_events`. The
new enum values need no schema change: `enum_column` uses
`native_enum=False`, and SQLAlchemy 2.x defaults `create_constraint=False`, so
these columns are plain `VARCHAR(48)` with validation in the application layer.
`alembic check` is clean.

**Limitations — what is NOT proven.**

- **The real Stripe adapter has never run against Stripe.** Every test uses
  `FakePaymentProvider`. The fake mirrors the real signature scheme exactly, so
  the *application* logic is proven; Stripe's actual API shape, its error
  responses, and the live webhook format are not. Confirming those needs a test
  account and `stripe listen`.
- No checkout or billing-portal endpoint is exposed yet. The provider methods
  exist and are tested against the fake, but nothing in the API calls them —
  a customer cannot yet self-serve an upgrade. That belongs with M14.
- `STRIPE_PRICE_PLANS` is unset by default, so no price maps to a plan and a
  subscription's plan is never changed by a webhook. Real deployments must map
  their own price ids.
- Plan *limits* are modelled (`PlanCapabilities`) and the gate checks
  `max_numbers`, but minute limits and overage behaviour are not enforced —
  that is M11.

### M5 — Redis (COMPLETE)

One invariant governs the package: **PostgreSQL is the source of truth; losing
Redis degrades latency, never correctness.** The proof is that the suite runs
with `REDIS_ENABLED` both ways and passes identically on correctness.

`app/cache/` holds a fail-soft client whose central design choice is that a
cache *miss* and a cache *outage* are different answers. `UNKNOWN` is a distinct
return value, so no caller can mistake "I could not reach Redis" for "there is
nothing there" — the mistake that would let a replayed webhook through a dedupe
check, or two workers into one critical section.

The fallback direction is chosen per concern, and they do not all point the same
way. Locks **fail open**: the real guarantee is a partial unique index, and
refusing would trade a cache outage for a provisioning outage. Dedupe **falls
through** to the unique index on `(provider, event_id)`. Rate limiting **fails
closed** to the in-process limiter, because "the cache is down, let everyone
through" is not an acceptable reading of a cache failure on a form that spends
money.

Redis is a **non-critical** readiness dependency: an outage is reported in
`/readyz` under `degraded` but does not withdraw the replica, because a
withdrawn replica serves nobody.

### M6 — Production telephony (COMPLETE)

`/voice/inbound` returns TwiML; `/voice/init` serves per-call dynamic variables.
Both sit on the audible path, so both never raise, never wait, and never trust
the request. See `docs/TELEPHONY.md` for the call flow and disposition table.

TwiML is built with `xml.etree`, which is a security control rather than a style
choice: business names come from a signup form and caller ids come from the
PSTN, and an f-string would let a `<` in either restructure the document Twilio
then executes. XML injection here is remote control of a phone call.

Twilio's signature is verified **before** the dialled number selects a tenant,
and verification is mandatory whenever an auth token is configured — stricter
than the status callback, because this request decides whose agent answers.

The POC path is untouched and remains the default (`CALL_PATH=elevenlabs_native`).

### M7 — Shared vertical agents (COMPLETE)

`tenants.agent_mode` selects per tenant, so a migration moves customers in
verifiable batches. Shared agent ids are **configured, never discovered** —
nothing in the application can create one, because code that can create one can
create a second by accident, and two agents serving one vertical is a
split-brain where half the customers get a stale prompt.

Two guards exist because sharing creates a class of failure the per-tenant shape
does not have — an operation scoped to one tenant that damages all of them:

* **compensation never deletes a shared agent.** `agents.is_shared` is recorded
  on the row rather than inferred from the tenant's current mode, because a
  tenant migrated between modes would make that inference wrong exactly once,
  catastrophically.
* **resync refuses to run against a shared agent.** Pushing one tenant's prompt
  would overwrite the prompt every other tenant on that vertical is served by.

`uq_agents_elevenlabs_agent_id` became a partial index over dedicated agents
only — still catching two tenants adopting one private agent, while allowing the
sharing this module exists for.

### M8 — Config versioning (COMPLETE)

`ConfigVersionService` is the single implementation of "demote the old row,
promote the new one", shared by the provisioning step, the admin rollback and
the future config editor. Version numbers are never reused after a rollback:
reuse would make two different prompts share an identity and silently re-point
every call that cited the old one.

`calls.agent_config_version` is **stamped at ingestion, not joined at read
time** — joining "the live config" months later would answer with whatever is
live then, blaming a prompt published after the call for what it said.

Rollback takes no provider argument at all. That is the structural guarantee it
works while ElevenLabs is down, which is exactly when retiring a bad prompt is
most urgent. Pushing the restored prompt to the vendor is the separate,
retryable `resync-agent` action.

### M9 — Webhook security (COMPLETE)

Signature verification for all three providers, with constant-time comparison, a
signed timestamp inside the MAC (so a captured body cannot be refreshed under a
fresh timestamp), and public-URL reconstruction for deployments behind a proxy.

**Hardened during M18:** a signature that is *present and wrong* is now refused
in every environment. The environment-dependent leniency exists so a local
DRY_RUN loop works with no secret configured — that is an *absent* signature. A
wrong one is a forgery, a replay outside its window, or a rotated secret, and is
never benign.

Stored payloads are redacted before they are written
(`app/services/webhook_payload.py`): credentials by key pattern, transcripts
summarized rather than duplicated. A transcript kept here would be PII in a
second place with its own retention question, outliving the M12 policy.

### M11 — Usage metering (COMPLETE)

The ledger is append-only and a correction is a new row, possibly negative.
Rollups are **recomputed, never incremented** — incrementing twice is a silent
overcharge nobody can later prove happened; recomputing converges however many
times it runs, which is what makes a late webhook harmless.

Idempotency is the unique index on `(provider, provider_reference)`, and the
insert runs inside a **savepoint** so a duplicate cannot poison the caller's
transaction and roll back the summary and notification sharing that session.

Metering informs warnings and, on the trial plan, enforcement. It never decides
entitlement: `billing_gate` reads `subscriptions` and does not import this
module, so a metering bug has no path to authorizing a purchase.

### M12 — PII and retention (COMPLETE)

Retention **redacts rather than deletes rows**. A call's transcript, caller
number and summary are erased; the row survives carrying duration, timing and
tenant — the billing skeleton, which identifies nobody. A vanished row is the
wrong answer to both "did you delete my data?" and "why am I billed for this?".

`PROTECTED_FROM_ERASURE` names the tables that must never be touched. Nothing
sweeps by pattern, so a table added later is considered deliberately rather than
swept into an erasure or silently missed by one.

*A bug worth recording:* the first implementation set `transcript_json = None`,
which SQLAlchemy renders as the JSON value `null` — not SQL NULL, and still
matching `IS NOT NULL`. The sweep would have run nightly, reported success and
deleted nothing, forever. It now uses `sqlalchemy.null()`, and a test asserts
the row is genuinely redacted.

### M13 — Notifications (COMPLETE)

A durable outbox. The row exists **before** any send is attempted, because the
alternative has a failure mode indistinguishable from success: the provider is
down, an exception is logged, and nobody learns the customer was never told.

Deduplication is a unique index on a `dedupe_key` derived from the event being
announced, so two workers racing one redelivered webhook collide in the database
instead of both emailing. Only the template id and its variables are stored —
never the rendered body, which would duplicate customer content and freeze
wording a later deploy was fixing.

A rendering failure is classified permanent; a vendor failure is transient.

### M14 — Customer dashboard (COMPLETE)

`/login` and `/dashboard`. The tenant id comes from the session's memberships
and every request is re-authorized server-side; a dashboard that trusted an id
from the URL would be an IDOR. Everything on the page is read-only, which keeps
the blast radius of a bug on the most exposed surface as small as possible.

### M15 — Admin panel (COMPLETE)

`/admin`. The operator key is held in component state and **never persisted** —
an XSS bug on a page that stored it would hand an attacker the ability to
release customers' phone numbers. Abandon names its consequence before it runs;
retry does not prompt, because it is idempotent and prompting for it would train
operators to click through the prompts that do matter.

### M16 — Observability (COMPLETE)

A dependency-free metrics registry exposed at `/metrics` in Prometheus format,
alongside the structured logging and correlation ids that already existed.

Every metric is declared at startup so a panel reads `0` rather than "no data" —
the difference between "nothing is failing" and "the exporter is broken", which
is not a distinction to be making at 3am. **Label values are bounded**: a tenant
id as a label is an unbounded cardinality explosion, so identifiers go in logs
and traces while labels carry only small closed sets.

Tests assert the negative space too: no tenant id, caller number or call sid
appears in `/metrics`, and no caller number appears in the inbound-call logs.

### M17 — Outage handling (COMPLETE)

A per-vendor circuit breaker. Its value is not that the vendor recovers sooner;
it is that our fallback runs in milliseconds instead of after a 30-second
timeout — the difference between a caller hearing a recorded apology and a
caller hearing nothing.

Wired into `/voice/inbound`: when the voice vendor's circuit is open the call is
answered with a voicemail rather than handed to a stream that will not connect.
Checked *before* the TwiML is built, because once `<Connect><Stream>` is in
Twilio's hands the call has left us.

Breakers are per process. A shared breaker in Redis would let a cache problem
open every circuit at once — a cache outage escalating into a total vendor
outage.

### M18 — Security and load testing (COMPLETE)

`tests/test_security_suite.py` attacks the running application from outside:
authentication bypass, tenant enumeration, IDOR, forged and replayed webhooks,
injection, malformed bodies, rate limiting, and concurrency on the paths that
spend money — including a burst of workers racing one provisioning run to prove
only one number is ever bought.

Every test is written as an attack that must fail rather than a feature that
must work, because a feature test would keep passing if authorization were
removed entirely.

This suite is what found the present-but-wrong signature gap recorded under M9.

### M20 — Full local E2E (COMPLETE)

`tests/test_full_e2e.py` walks signup → billing gate → LLM config → number →
agent → link → verify → ACTIVE → inbound call → webhook → call record →
transcript → summary → notification → usage → dashboard → admin, against the
real database and the real HTTP stack.

The failure scenarios carry equal weight: a call to a number we do not own, a
vendor outage mid-journey, a triple-delivered webhook that must produce one call
and one usage event and one email, a forged webhook that reaches nothing, and
another tenant's grant opening nothing. See `docs/E2E_TESTING.md`.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **No container runtime** (§1) | Blocks validation of M2–M20. Operator decision required. |
| Writing unvalidated infrastructure | Anything written without its service running is marked *written, not validated* and never reported as working. |
| Temporal migration loses recovery semantics | Keep the state machine until Temporal tests pass; run both behind a flag; delete only after. |
| Redis quietly becomes authoritative | Every Redis path gets a Postgres-fallback test; a CI job runs the suite with Redis disabled. |
| Billing gate added after provisioning works | Gate lands with M10 *before* any real-credential integration testing is enabled. `DRY_RUN=true` remains the default until then. |
| Real credentials present in `.env` | Never printed, committed, logged, or returned. Integration tests against real vendors are explicit, opt-in commands — never part of the default suite. |
| Scope: 20 modules is a multi-week program | Sequenced by risk so that stopping early still leaves the highest-value protections in place. |

---

## 7. Testing strategy

The suite must stay runnable with no network and no vendor credentials — that
property already exists and is the single most valuable thing in the repo.

- Unit / service tests: fakes only, no network. Run today.
- Database, API, provisioning tests: real PostgreSQL via the migrated scratch
  database fixture. **Blocked on §1.**
- Temporal tests: `WorkflowEnvironment` time-skipping, fake activities.
- Redis tests: real Redis, plus a Redis-down variant.
- Security tests: cross-tenant access attempts, authorization failures,
  webhook signature rejection, secret-redaction assertions.
- Load tests: `/voice/inbound` and `/voice/init`, 500 concurrent, p50/p95/p99.
- Tests never purchase numbers, never send mail, never charge cards. Enforced by
  `DRY_RUN` forcing fakes, not by convention.

## 8. Rollback strategy

Every module is independently revertable: expand/contract migrations (never a
destructive column drop in the same release), feature flags for Temporal vs
state machine and for per-tenant vs shared agents, and config versioning that
makes a bad prompt a flag move rather than a vendor call. The repository stays
runnable after every module — verified by running lint, types, tests, migrations
and boot at each module boundary.
