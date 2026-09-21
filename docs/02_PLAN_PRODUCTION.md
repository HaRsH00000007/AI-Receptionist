# Plan B — Production

**Goal:** a billable, multi-tenant SaaS that can be sold, that survives a vendor outage, and that
nobody has to babysit. Assumes Plan A shipped and its state machine exists.

**Effort:** ~8–10 weeks after the POC, 2 devs (1 backend-heavy, 1 full-stack).
**Infra cost:** ~$300–600/mo before vendor usage (Temporal Cloud, RDS, Redis, ECS/Fly, Sentry).

---

## What changes vs the POC

| Area | POC | Production |
|---|---|---|
| Orchestration | Postgres state machine + ARQ | **Temporal Cloud** — same steps become activities |
| Agent model | One ElevenLabs agent per tenant | **Shared agent per vertical + per-call dynamic variables** |
| Call path | ElevenLabs native Twilio import | **Our TwiML `<Connect><Stream>`** — we own media, records, routing |
| Cache/queue | none | **Redis** — idempotency, locks, rate limit, hot config, pub/sub |
| Billing | none | **Stripe** — plans, metered minutes, gate before provisioning |
| Auth | magic link | Password + magic link (+ OAuth later), sessions, org/roles, audit log |
| Deploy | single container | ECS/Fly, ≥2 API replicas, blue/green, IaC |
| Secrets | env vars | AWS Secrets Manager / Doppler, quarterly rotation |
| Observability | Sentry + logs | OpenTelemetry traces, metrics, SLO dashboards, on-call alerts |

---

## 1. Temporal migration (week 1–2)

Each POC step becomes an activity; the state machine becomes a workflow:

```python
@workflow.defn
class ProvisionTenant:
    async def run(self, tenant_id: UUID):
        cfg    = await workflow.execute_activity(generate_config, tenant_id, ...)
        try:
            num = await workflow.execute_activity(purchase_number, tenant_id, cfg.area_code, ...)
        except ActivityError:
            await workflow.execute_activity(notify_manual_provisioning, tenant_id)
            raise
        try:
            agent = await workflow.execute_activity(ensure_agent, tenant_id, cfg, ...)
            await workflow.execute_activity(link_number, num, agent, ...)
            await workflow.execute_activity(verify_link, num, agent, ...)
        except ActivityError:
            await workflow.execute_activity(release_number, num)   # saga compensation
            raise
        await workflow.execute_activity(activate_and_notify, tenant_id)
```

Wins: retries/backoff/timeouts declarative, compensation explicit, full execution history per tenant,
signals for "user edited config mid-provision", and a `resync` workflow you can fan out over every
tenant when a prompt template changes.

Keep the `provisioning_runs` table as a **read model** projected from Temporal so the admin panel and
customer dashboard don't query Temporal directly.

---

## 2. Shared-agent architecture (week 2–3)

Replace N tenant agents with ~5 vertical agents (salon, legal, medical, real estate, generic).

```
Inbound call → Twilio → our /voice/inbound (TwiML)
                             │  lookup dialed number → tenant  (Redis, <50ms)
                             ▼
                  <Connect><Stream> → ElevenLabs shared agent
                             │
   ElevenLabs conversation-initiation webhook → /voice/init
                             │  returns dynamic variables:
                             │  business_name, services, hours, greeting, escalation,
                             │  voice_id, timezone, recording_notice
                             ▼
                        live conversation
```

- Prompt improvements ship once, to everyone, instantly.
- Config rollback = bump `config_version` back; no vendor calls at all.
- `/voice/init` is on the call path → hard 300ms budget, Redis-backed, with a **stale-but-serving
  fallback** (serve last-known config if Postgres is slow) and a generic safe config if all else fails.
  A caller must never hear silence because our cache missed.

Migration: run both models side by side, move tenants in batches behind a `tenant.agent_mode` flag,
verify with a scripted test call per batch, then delete the per-tenant agents.

---

## 3. Redis, precisely (week 3)

| Key pattern | Purpose | TTL |
|---|---|---|
| `num:{e164}` → tenant_id | Call-path lookup | 300s, invalidate on number change |
| `cfg:{tenant_id}:v{version}` | Rendered agent config | 600s, version-keyed so no invalidation race |
| `wh:{provider}:{event_id}` | Webhook dedupe (SETNX) | 24h, backed by `webhook_events` table |
| `lock:provision:{tenant_id}` | Single-flight provisioning | 300s |
| `rl:signup:{ip}` / `rl:api:{tenant}` | Rate limiting | sliding window |
| `events:tenant:{id}` | Pub/sub → SSE dashboard | n/a |

Policy: **Postgres is always the source of truth; Redis loss degrades latency, never correctness.**
Prove it — a staging test that runs the full suite with Redis flushed on every request.
Single-flight on `cfg:` to avoid stampedes. Nothing auth- or billing-related is ever cached.

---

## 4. Billing and cost control (week 4) — highest commercial priority

- Stripe Checkout + customer portal; plans Starter / Pro / Enterprise mapped to
  `included_minutes`, `max_numbers`, `feature_flags`.
- **No number is purchased before a card is on file or an explicit trial grant.** Today's flow spends
  real money on every anonymous form submit — this is the single biggest leak.
- Metered usage: ElevenLabs minutes + Twilio minutes reconciled nightly against provider usage APIs
  and reported to Stripe. Never trust in-app counters alone.
- Overage: soft warning at 80%, hard behavior at 100% configurable per plan (block vs bill).
- **Number reaper:** nightly job releases numbers for tenants cancelled >30d or never activated >7d,
  with a dry-run report before it deletes anything.
- A weekly cost-per-tenant report — margin per client is the number Roman will actually ask for.

---

## 5. Reliability

- **Vendor outage playbook:** ElevenLabs down → TwiML fallback that plays a recorded message and
  takes voicemail, then transcribes it. A caller must never get a dead line.
- Circuit breakers per vendor; health checks; `/healthz` + `/readyz` separated.
- ≥2 API replicas, rolling deploys, DB migrations backwards-compatible (expand/contract).
- Postgres PITR + weekly restore drill. Backups you haven't restored aren't backups.
- Idempotency everywhere on the write path; `Idempotency-Key` header supported on public POSTs.
- Load target: 500 concurrent calls, 50 provisionings/min. Load-test `/voice/init` specifically —
  it's the only endpoint where latency is audible to a customer.

---

## 6. Security & compliance (week 5–6)

- All provider keys in a secret manager, rotated quarterly; the exposed ElevenLabs key rotated day 1.
- Twilio and ElevenLabs webhook **signature validation** — mandatory, not optional. An unsigned
  post-call webhook endpoint is a free way to poison every customer's dashboard.
- Row-level tenant isolation enforced in a single repository layer, plus a test that every query
  filters by `tenant_id`.
- PII: transcripts and caller numbers are sensitive. Encrypt at rest, retention policy per plan
  (e.g. 90d default), and a working delete-my-data path.
- Recording consent announcement injected into the greeting per state law where recording is on.
- A2P 10DLC brand/campaign registration if SMS notifications ship.
- Audit log for every config change, number purchase/release, and admin action.
- Annual pen test / dependency scanning in CI.

---

## 7. Product surface (week 6–8)

**Client dashboard:** live call feed, transcripts + summaries, messages inbox with callback,
config editor with preview + "test call to my phone", business hours & after-hours behavior,
number management, usage vs plan, notification settings, team members.

**Admin panel:** every provisioning run and its Temporal history, per-tenant health, manual retry /
release / resync, cost per tenant, vendor status board, feature flags, impersonation with audit.

**Quality loop:** sample N% of calls, LLM-judge them against a rubric (did it answer correctly? did
it capture the callback number? did it escalate when it should?), track the score per vertical over
time. This is what turns prompt tuning from vibes into a metric, and it's the moat.

---

## 8. Observability

- OpenTelemetry traces spanning API → workflow → vendor call, keyed by `tenant_id` and `call_id`.
- Metrics with alerts: provisioning success rate, time-to-active p50/p95, `/voice/init` p99,
  call answer rate, agent error rate, webhook lag, LLM schema-failure rate, cost per call.
- SLOs: 99.5% provisioning success within 5 min; 99.9% inbound call answer; `/voice/init` p99 <300ms.
- Alerts route to a real on-call channel. **A failed provisioning must page someone.** The entire
  reason the current system fell over is that failures were silent.

---

## 9. Phasing

| Weeks | Focus |
|---|---|
| 1–2 | Temporal migration, read models, activity idempotency |
| 2–3 | Shared-agent architecture + `/voice/init` + own media path |
| 3 | Redis, caching policy, rate limits, webhook dedupe |
| 4 | Stripe, provisioning gate, metering, number reaper, cost reporting |
| 5–6 | Security hardening, webhook signatures, PII retention, compliance |
| 6–8 | Client dashboard + admin panel + quality scoring loop |
| 8–10 | Load testing, outage playbooks, IaC, restore drill, launch |

---

## 10. Team-scaling note

Everything above assumes the codebase enforces its own rules: one repository layer that every query
goes through, one vendor-client interface with fakes, one idempotency helper, one config renderer.
The Make.com system failed because a *single* duplicate column silently changed the meaning of every
downstream step. The structural answer is typed schemas at every boundary and a test suite that runs
the full provisioning path with no network — so a change like that fails in CI in 30 seconds instead
of in production for a week.
