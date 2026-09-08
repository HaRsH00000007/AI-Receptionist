# Plan A — POC / MVP

**Goal:** one clean, observable, end-to-end run. A business submits the form and within ~2 minutes
has a working dedicated number that an AI answers correctly — with every step visible in an admin
panel and individually retryable. This is explicitly the thing Make.com never achieved.

**Not the goal:** billing, multi-region, SSO, high availability, per-client voices, Temporal.

**Effort:** ~3 weeks for one full-stack dev; ~2 weeks with a second dev on the frontend.
**Infra cost:** ~$20–40/mo + Twilio/ElevenLabs usage.

---

## Scope

| In | Out |
|---|---|
| Signup form (own UI or Tally webhook) | Stripe / plan enforcement |
| LLM config generation from form data | Per-client custom voices |
| Twilio number search + purchase with fallback ladder | Temporal |
| ElevenLabs agent creation from template | Redis |
| Number ↔ agent link, verified | Multi-region / autoscaling |
| Inbound calls answered live | Call recordings UI |
| Post-call transcript + LLM summary + email | Team accounts / RBAC |
| Admin panel: run state, step errors, retry button | Outbound demo call *(stretch)* |
| Magic-link login + basic client dashboard | |

---

## Architecture

```
Next.js  ──POST /api/v1/signups──►  FastAPI  ──►  Postgres
                                       │              ▲
                                       │ enqueue      │ state
                                       ▼              │
                              ARQ worker (provisioning state machine)
                                       │
              ┌────────────────┬───────┴────────┬────────────────┐
              ▼                ▼                ▼                ▼
         LLM (config)     Twilio (buy)   ElevenLabs (agent)  Email (Resend)

Inbound call ──► Twilio ──► ElevenLabs agent ──► post-call webhook ──► FastAPI
                                                                        │
                                                              LLM summary → email + dashboard
```

Single FastAPI process + single worker process. Both deployed on Railway/Render/Fly.

---

## Data model (POC — 8 tables)

```sql
tenants            id, name, business_type, contact_email, contact_phone, area_code,
                   plan, timezone, status, created_at
business_profiles  id, tenant_id, raw_form_json, services, hours_raw, hours_json,
                   greeting_style, escalation_raw, escalation_json, config_version
agent_configs      id, tenant_id, version, system_prompt, first_message, voice_id,
                   model_params_json, generated_by, is_live, created_at
phone_numbers      id, tenant_id, e164, twilio_sid, area_code, status,
                   purchased_at, released_at
agents             id, tenant_id, elevenlabs_agent_id, agent_config_id, status, synced_at
provisioning_runs  id, tenant_id, status, current_step, attempt, last_error,
                   correlation_id, started_at, finished_at
provisioning_steps id, run_id, step_name, status, idempotency_key, request_json,
                   response_json, error, attempt, started_at, finished_at
calls              id, tenant_id, provider_call_id, direction, from_e164, to_e164,
                   started_at, duration_s, transcript_json, summary, caller_name,
                   callback_number, intent, urgency, status
```

Constraints that matter:
- `UNIQUE (tenant_id) WHERE status = 'active'` on `phone_numbers` — a tenant cannot own two live numbers.
- `UNIQUE (run_id, step_name)` on `provisioning_steps` — a step exists once per run.
- `UNIQUE (provider_call_id)` on `calls` — webhook replay safe.

---

## The provisioning state machine

```
DRAFT
  → VALIDATED          validate + normalize form, derive timezone from area code
  → CONFIG_GENERATED   LLM: form → structured agent config (schema-validated)
  → NUMBER_PURCHASED   Twilio search ladder → buy → record SID
  → AGENT_CREATED      ElevenLabs: create agent from rendered prompt
  → NUMBER_LINKED      import number to ElevenLabs, assign agent
  → VERIFIED           read back both objects; confirm the link resolves
  → ACTIVE             welcome email sent, dashboard live

FAILED(step)           terminal-for-now, retryable from admin
COMPENSATING           release number / delete agent when abandoning
```

Implementation rules:

1. **One step = one function**, signature `async def step(run: Run, ctx: dict) -> dict`. Pure w.r.t.
   our DB; all side effects wrapped in an idempotency check.
2. **Idempotency key** per step = `sha256(run_id || step_name || attempt_group)`. Before any
   purchase, check Twilio for a number already tagged `friendly_name = tenant:{id}` and adopt it
   instead of buying again. This is the guard against double-spend on retry.
3. **Worker claims work** with `SELECT ... FROM provisioning_runs WHERE status IN (...) AND
   next_attempt_at <= now() ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 5`.
4. **Retry policy**: exponential backoff 5s → 30s → 2m → 10m, max 5 attempts, then `FAILED` + alert.
   Distinguish retryable (429/5xx/timeout) from terminal (4xx validation) — never retry a 400.
5. **Verification is a real step**, not an assumption. Aman's #1 remaining item is "link step not
   verified" — so we read the objects back from both vendors and assert the link before declaring ACTIVE.
6. **Compensation**: a nightly job releases Twilio numbers whose tenant is `abandoned`/`failed` for
   >24h, and deletes orphan ElevenLabs agents with no live number.

---

## LLM usage in the POC (exactly two calls)

**Call 1 — config generation (once per tenant, strong model).**
Input: business name, type, services, raw hours, greeting style, escalation rules, timezone.
Output: strict JSON — `{system_prompt, first_message, services[], hours[{day,open,close}],
faq[{q,a}], escalation{mode, notify_email, notify_phone, conditions[]}, voice_id}`.
Validated with Pydantic; on schema failure, one repair retry, then fall back to a **deterministic
template render** of the raw form fields so signup never hard-blocks on the LLM.

**Call 2 — post-call summary (once per call, small/fast model).**
Input: transcript. Output: `{summary, caller_name, callback_number, intent, urgency:1-5,
needs_human:bool}`. Callback number cross-checked against Twilio's caller ID before it's shown.

The prompt template lives in the repo as a versioned file. `agent_configs.version` records which
template produced a config, so we can re-render every tenant when the template improves.

**No LLM anywhere near number selection.** See `00_DECISIONS.md` §3.

---

## Admin panel (the whole point)

A single page listing runs with: tenant, status, current step, attempt, last error, elapsed time.
Click a run → per-step timeline with request/response JSON and the exact vendor error string.
Buttons: **Retry step**, **Retry from step**, **Abandon + compensate**, **Force resync agent**.

This is what makes the difference from Make.com. If a run fails at 3am, someone sees it at 9am with
the error in front of them instead of discovering a week later that leads were dropped.

---

## Week-by-week

**Week 1 — spine**
- Repo scaffold (FastAPI + Alembic + Next.js), Docker Compose for local Postgres
- Data model + migrations
- `POST /api/v1/signups` with Pydantic validation + rate limit; accepts our own form *and* a Tally webhook
- Provisioning run/step tables + worker loop + retry/backoff, with **all vendor calls faked**
- End-to-end run passes with fakes; admin panel v0 (table + step detail)

**Week 2 — vendors**
- Twilio client: search ladder, purchase, release; sandbox/test creds first
- ElevenLabs client: create agent, import number, assign, read back
- LLM config generation + schema validation + deterministic fallback
- Verification step + compensation job
- **First real end-to-end: one number bought, one agent live, one real inbound call answered**

**Week 3 — the loop closes**
- ElevenLabs post-call webhook → `calls` → LLM summary → notification email
- Magic-link auth + client dashboard (my number, my config, my calls, my messages)
- Config edit → regenerate → resync agent
- Admin retry/abandon actions wired
- Sentry, structured logs with `correlation_id`, alert on any `FAILED` run
- One-time importer for the existing Google Sheet + reconciliation report vs Twilio/ElevenLabs

**Stretch:** outbound demo call right after signup (Flow B).

---

## Testing

- Vendor clients behind a `Protocol` interface with fake implementations; the whole state machine
  runs in CI with no network.
- `DRY_RUN=true` mode: real LLM, fake purchases — safe for demos.
- Contract tests recorded against Twilio/ElevenLabs test credentials.
- One manual pre-launch checklist: submit form → answer the call yourself → confirm the email lands.

---

## Definition of done

1. Fresh form submission → ACTIVE with zero manual steps, three times in a row.
2. A deliberately failed step (bad API key) surfaces in the admin panel within 60s and succeeds on retry.
3. Killing the worker mid-run loses nothing — it resumes on restart.
4. Submitting the same form twice does not buy two numbers.
5. A real inbound call is answered with the correct business name, services and hours.
6. The post-call summary email arrives within 2 minutes of hangup.
