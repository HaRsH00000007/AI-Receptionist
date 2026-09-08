# AI Receptionist — Architecture Decisions & Open Questions

_Owner: internal build team. Replaces the Make.com automation chain built by Aman._

---

## 1. What the product actually is

Two distinct call flows exist. Aman's chain only covers the first one.

| Flow | Direction | Trigger | Purpose |
|---|---|---|---|
| **A — Live receptionist** | Inbound | A real customer calls the business's dedicated number | AI answers, greets, explains services, takes a message, escalates |
| **B — Demo / verification call** | Outbound | Immediately after signup form submit | Platform calls the signup's contact number so they *hear* their agent within ~60s |

Flow B is the conversion moment on upfirst.ai and is **missing from the current build**. It needs
ElevenLabs outbound calling (via the Twilio integration), not just number provisioning. It also
doubles as phone verification, which we need anyway to stop abuse (see §7).

**Open question for Roman:** confirm Flow B is in scope for v1. It changes the sequencing (a demo
call can happen on a shared pool number *before* we spend money on a dedicated number).

---

## 2. Does Make.com create a separate ElevenLabs agent per user?

Yes — Aman's description ("customizes a template agent with the business name, services, hours, and
greeting") means the scenario **clones/creates one ElevenLabs Conversational AI agent per client**.
That is one agent object per tenant, each with its own baked-in system prompt.

**This is the wrong long-term shape**, and it is a big part of why the system is brittle:

- Prompt improvements must be re-pushed to N agents; they drift immediately.
- Every agent is a mutable object living in a vendor's account with no version history in our DB.
- Nothing on our side can answer "what prompt was live when this call happened?"
- Agent creation is on the critical signup path, so a vendor hiccup blocks the whole funnel.

### Recommended shape

| Stage | Design |
|---|---|
| **POC** | Keep one agent per tenant (matches what exists, lowest risk). But **generate the prompt in our code from a versioned template**, store the rendered prompt + template version in Postgres, and treat ElevenLabs as a *projection* of our DB, not the source of truth. Add a `resync_agent()` that can re-push any tenant at any time. |
| **Production** | Move to **one shared agent (per business-vertical, ~5 agents total) + per-call dynamic variables**. ElevenLabs supports a *conversation-initiation webhook*: on every inbound call ElevenLabs calls our API with the dialed number, and we return that tenant's name, services, hours, greeting, voice and tools as dynamic variables. Prompt fixes then ship once, instantly, to every client. |

The shared-agent model also kills the whole "agent creation failed mid-signup" failure class — there
is nothing to create.

**Caveat:** if clients are promised custom *voices* or per-client knowledge bases/RAG, you still need
per-tenant objects for those. Voice can be a dynamic variable; a knowledge base cannot. Confirm with
Roman what "custom agent" means commercially before locking this in.

---

## 3. Should an LLM pick the phone number / area code? — **No.**

This is the one idea in the current plan I'd cut outright.

Area code is a **field the user typed on the form**. Buying a number is:

```
twilio.available_phone_numbers("US").local.list(area_code=805, voice_enabled=True)
→ pick first → purchase
```

That is a deterministic API call with a deterministic fallback ladder:

1. Exact area code requested
2. Any area code in the same state (`in_region=CA`)
3. Nearest-by-distance area code (`near_number` / `in_locality`)
4. Toll-free
5. Fail loudly → human queue, notify the customer

Putting an LLM in that path buys you nothing and costs you: nondeterminism on a **money-spending
irreversible action**, extra latency, a new outage dependency, and a failure mode where a
hallucinated area code silently buys a number in the wrong state. If you ever need "which area code
is closest to Naperville, IL" — that is a lookup table, not a reasoning task.

**Rule of thumb for this whole system:**
> LLMs handle *language*. Deterministic code handles *money, infrastructure, and state transitions.*

### Where an LLM genuinely earns its place

| # | Job | Why LLM | Guardrail |
|---|---|---|---|
| 1 | **Form → agent config**: turn messy free text ("we do cuts, color, walk-ins sometimes") into a structured system prompt, greeting, FAQ list, service list | Genuine language task; this is the core product quality lever | Structured Outputs / JSON schema + Pydantic validation + regeneration on failure |
| 2 | **Operating-hours normalization**: "Mon–Fri 9–6, Sat till 2, closed Sun" → structured JSON schedule + timezone | Free text is unbounded | Schema-validated; deterministic renderer turns JSON back into prose for the prompt |
| 3 | **Business-type routing** when the user picks "Other" | Picks which prompt template to use | Constrained to the enum of templates we have |
| 4 | **Post-call intelligence**: summary, caller name, callback number, intent, urgency score, "did the AI fail?" flag | This is what makes the dashboard worth paying for | Callback number cross-checked against Twilio caller ID; never used to auto-dial without review |
| 5 | **Escalation decision** from "Escalation Rules" free text → structured rules (when to text the owner, when to take a message) | Language → policy | Rules compiled to JSON once at config time, then evaluated deterministically at runtime |
| 6 | **Config QA judge** (optional): score the generated prompt before it goes live; below threshold → hold for human review | Cheap safety net | Advisory only |

Item 5 is the important pattern: **the LLM compiles policy once, deterministic code executes it on
every call.** Never let an LLM make the same decision 10,000 times when it can make it once.

The runtime conversation LLM is ElevenLabs' own — that is a separate concern and already handled.

**Model choice:** config generation is a quality-critical, low-volume, once-per-tenant call → use the
strongest model available (this is where prompt quality shows up in every future call). Post-call
summarization is high-volume and easy → use a small/fast model. Two different tiers, not one.

---

## 4. Orchestration: Temporal, or a Postgres state machine?

The real disease with Make.com isn't Make.com — it's that a **multi-step process with irreversible
external side effects was built with no durable state, no idempotency, and no visibility.** A
duplicate spreadsheet column silently corrupted every downstream step for days. Any replacement must
fix that *class* of problem, not just move it to Python.

Non-negotiables regardless of tool:

- Every provisioning run is a **row**, with a status, per-step rows, attempt counts, and last error.
- Every external call carries an **idempotency key**; retries never double-buy a number.
- Every failure is **visible** (admin panel + alert), never silent.
- Every step is **individually retryable** from the admin panel.
- Compensation exists: if agent creation fails after purchase, we either retry or **release the
  number** — an orphaned Twilio number bills monthly, forever.

### Recommendation

| Stage | Choice |
|---|---|
| **POC** | **Postgres state machine + a worker loop.** `provisioning_runs` + `provisioning_steps` tables, claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, driven by an ARQ (or plain asyncio) worker. ~4 steps, ~300 lines. No new infrastructure, fully debuggable with SQL. |
| **Production** | **Temporal** (Temporal Cloud, so you don't operate it). Retries, backoff, timeouts, compensation (saga), replay and history come free, and the "what happened to tenant X" question becomes a UI click. |

Design the POC state machine so each step is a pure function `(run_id, ctx) -> ctx`. Those map 1:1
onto Temporal activities later, so the migration is mechanical rather than a rewrite.

**Do not use `FastAPI BackgroundTasks` for provisioning.** They die with the process and you get the
exact same silent-drop failure you have today.

---

## 5. Redis and caching

You asked directly, so: **POC — no Redis. Production — yes, but not primarily as a cache.**

For the POC there is nothing worth caching (a few hundred rows) and Postgres is a perfectly good
queue at this volume. Adding Redis now is one more thing to run and one more thing that can be down.

In production, Redis earns its place for five specific jobs:

| Job | Why | Notes |
|---|---|---|
| **Webhook idempotency / dedupe** | Twilio, ElevenLabs and Stripe all retry. Processing a post-call webhook twice = two emails, double billing | `SETNX webhook:{provider}:{event_id}` with 24h TTL, *plus* a `webhook_events` table as the durable record. Redis is the fast path, Postgres is the truth |
| **Distributed lock on provisioning** | Two workers must never buy two numbers for one tenant | `SET lock:provision:{tenant_id} NX EX 300`. Belt-and-braces with a DB unique constraint |
| **Rate limiting** | Form spam → real money spent on Twilio numbers. Per-IP, per-email, per-tenant API | Sliding window in Redis |
| **Hot-path config lookup** | The conversation-initiation webhook (§2) sits in the call path. `dialed_number → tenant config` must answer in <50ms or the caller hears dead air | This is the only true *cache* use, and it's the important one |
| **Pub/sub for the live dashboard** | SSE/WebSocket fan-out of call events across API replicas | Optional; can be polled at first |

### Cache policy (the part you said you hadn't touched)

Keep it boring and write it down once:

- **Postgres is always the source of truth. Nothing is cache-only.** If Redis is empty, everything
  still works, just slower. Test this by flushing Redis in staging.
- **Key by version, not just id:** `tenant:{id}:cfg:v{config_version}`. Publishing a new config bumps
  the version, so the old key is unreachable — no invalidation race, and rollback is instant.
- **Invalidate on write** (delete the key in the same transaction-commit hook), with a **short TTL
  (60–300s) as a safety net** in case an invalidation is missed.
- **Never cache**: auth decisions, billing state, anything you'd be embarrassed to serve 5 minutes stale.
- **Do cache**: rendered agent config, tenant lookup by phone number, plan limits, static reference
  data (area code → state/timezone).
- **Stampede protection**: single-flight lock or jittered TTLs on the hot config key.

At the volumes this product will realistically see in year one, an in-process TTL cache in the API
plus Postgres would also do. Redis becomes genuinely necessary once you run more than one API replica.

---

## 6. Call path: how the number actually reaches the agent

Two options for the "link" step Aman never verified:

**Option A — ElevenLabs native Twilio import (POC).**
`POST /v1/convai/phone-numbers` with the Twilio SID/number → assign agent id. ElevenLabs owns the
media path. Fastest to working.

**Option B — We own the media path (production).**
Twilio voice webhook → our FastAPI → return TwiML `<Connect><Stream>` to ElevenLabs' websocket.

Option B costs a few days and buys: per-call dynamic variables, our own call records and recordings
independent of the vendor, the ability to reject/route calls before spending agent minutes, and a
realistic path to swapping voice vendors later without re-provisioning every client's number.

Recommendation: **A for POC, B for production**, but note that Option A + the
conversation-initiation webhook already gets you most of the dynamic-variable benefit, so B is not
urgent. Decide it on whether call recording/ownership is a commercial requirement.

---

## 7. Things nobody has flagged yet that will bite

1. **Money leak.** Today, anyone who submits the form gets a real Twilio number bought for them —
   ~$1.15/mo each, forever, plus per-minute voice and ElevenLabs minutes. There is no payment gate.
   **Provision only after card-on-file or a real trial gate**, and run a nightly reaper that releases
   numbers for cancelled/abandoned tenants. This is the highest-value fix on the list and it's a
   business fix, not a technical one.
2. **A2P 10DLC.** If notifications go out by SMS from a US long code, the sending brand/campaign must
   be registered or messages get filtered. Voice-only is unaffected. Decide SMS vs email-only for v1.
3. **Call recording consent.** Two-party-consent states (CA, FL, PA, WA, IL…) require an announcement
   if calls are recorded. The greeting must include it, per state, if recording is on.
4. **Exposed ElevenLabs key** (already flagged by Aman) — rotate, move to a secret manager, and never
   put a provider key anywhere the browser can see it.
5. **Phone verification.** Flow B's demo call must be gated by an OTP or rate limit, or the form
   becomes a free robodialer for anyone who finds it.
6. **Migration.** The existing Google Sheet rows (including the ones corrupted by the duplicate
   column) need a one-time import + reconciliation against what Twilio and ElevenLabs actually think
   exists. Expect orphans in both directions.
7. **Timezones.** "9–6" means nothing without the business's timezone. Derive from area code, confirm
   on the form.

---

## 8. Stack (both plans)

- **Frontend:** Next.js (App Router) + TypeScript + Tailwind + shadcn/ui
- **API:** FastAPI + Pydantic v2 + SQLAlchemy 2.0 async + Alembic
- **DB:** Postgres (Neon/Supabase for POC, RDS/Cloud SQL for prod)
- **Worker:** ARQ (POC) → Temporal (prod)
- **Voice:** Twilio (numbers/PSTN) + ElevenLabs Conversational AI
- **LLM:** Anthropic or OpenAI via a thin provider-agnostic wrapper — see §3 for the two tiers
- **Email:** Resend or SendGrid
- **Payments:** Stripe (production; stub in POC)
- **Errors/metrics:** Sentry + structured JSON logs with a `correlation_id` per provisioning run

---

## 9. Open questions for Roman before code

1. Is the outbound demo call (Flow B) in v1 scope?
2. Does "custom AI agent" have to mean a custom *voice* per client, or just custom content?
3. SMS notifications, or email only, for v1?
4. Are calls recorded and stored? (drives §6 Option B and §7.3)
5. Is there a payment gate before number purchase, or is v1 free-trial-with-a-number?
6. Do we keep the Tally form for now, or rebuild it in Next.js immediately?
7. US-only, or international numbers too? (changes Twilio regulatory bundles significantly)
