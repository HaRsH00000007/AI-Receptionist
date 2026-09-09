# Live E2E test readiness

**Date:** 2026-09-10 · **Status:** ready to stage; **one blocker** and **one
manual vendor step** stand between here and a real inbound call.

The headline: the product chain is **complete and passing end to end** against
fakes. Nothing needs to be built for a live call. What is missing is
credentials, a public URL, and one piece of ElevenLabs dashboard configuration
that the application deliberately does not manage.

---

## 1. What is already working

`tests/test_end_to_end.py::test_the_whole_poc_runs_end_to_end` walks the entire
requested chain in a single test and passes today (part of 551 passing tests):

| # | Stage | Implementation | Proven by |
|---|-------|----------------|-----------|
| 1 | Signup | `POST /api/v1/signups` | returns `201`, status `draft`, in milliseconds |
| 2 | Business configuration | `generate_config` step, `config_generator.py` | asserted: our prompt is what the agent runs, not raw model output |
| 3 | Billing gate | `billing_gate` step + re-check inside `purchase_number` | asserted: `purchase_number` count `== 0` when unentitled |
| 4 | Twilio number provisioning | `purchase_number` step | number bought, requested area code honoured |
| 5 | ElevenLabs agent creation | `create_agent` step | agent id stored, config version 1 |
| 6 | Number linked to agent | `link_number` + `verify` steps | asserted: `assigned_agent_id` matches the agent |
| 7 | Inbound call | **ElevenLabs owns the media path** | see note below |
| 8 | Conversation to transcript | `POST /api/v1/webhooks/elevenlabs/post-call` | webhook creates the `calls` row |
| 9 | Post-call summary | `summarizer.py`, driven by `app.worker` | summary and intent asserted |
| 10 | Notification email | `notifications.send_call_summary` | email content asserted |

**The architectural fact that makes this feasible:** there is no Twilio
media-stream bridge to build, because ElevenLabs owns the media path. Twilio's
role is to own the number; the number is imported into ElevenLabs and assigned
to the agent, so an inbound call is answered by ElevenLabs directly. Twilio's
status callbacks are stored for the audit trail only. This is why stage 7
requires no code.

Also working: Temporal orchestration (M4), tenant isolation, auth, the Stripe
webhook, admin retry/abandon, and the status page.

---

## 2. What is missing for a real call

### BLOCKER: a public HTTPS URL

ElevenLabs must POST the post-call webhook to us. Nothing reaches `localhost`.
You need a tunnel (ngrok, Cloudflare Tunnel) or a deployed host. **This is the
only true blocker; everything else is configuration.**

### MANUAL VENDOR STEP: the webhook URL is not set by the application

Verified: nothing in `create_agent` or `config_generator` sends a webhook URL to
ElevenLabs, and there is no `PUBLIC_BASE_URL` setting anywhere. The post-call
webhook must be registered **by hand in the ElevenLabs dashboard** (workspace
webhook settings), pointing at:

```
https://<your-public-host>/api/v1/webhooks/elevenlabs/post-call
```

Take the signing secret it returns and set `ELEVENLABS_WEBHOOK_SECRET`.

> This is a real gap for productisation — self-serve onboarding cannot depend on
> a human editing a dashboard — but it does **not** block a controlled test.

### Two contract details that will silently break a live call

1. **Tenant resolution is by dialled number.** `webhooks.py::_tenant_for`
   matches the payload's `data.metadata.phone_call.agent_number` against
   `phone_numbers.e164`. If ElevenLabs reports the number in a different format
   than we stored it, the webhook is **rejected** with "no tenant owns the
   called number". Check this first if a call connects but no `calls` row
   appears.
2. **Signature verification is soft outside production.** At
   `ENVIRONMENT=local` a bad signature is logged and **still processed**
   (`if not signature_valid and settings.is_production_like`). Convenient for
   testing, but it means a green local test does not prove your webhook secret
   is correct.

---

## 3. Environment variables and credentials required

### Already set locally

`DATABASE_URL`, `REDIS_URL`, `TEMPORAL_*`, `ORCHESTRATOR=temporal`,
`AUTH_SECRET_KEY`, S3/MinIO, SMTP/Mailpit.

### Must be added for a live call

| Variable | Needed for | Notes |
|---|---|---|
| `DRY_RUN=false` | **arming everything** | see section 4 — this is the master switch |
| `TWILIO_ACCOUNT_SID` | Twilio | |
| `TWILIO_AUTH_TOKEN` | Twilio | |
| `ELEVENLABS_API_KEY` | agent creation and phone import | |
| `ELEVENLABS_WEBHOOK_SECRET` | post-call webhook | from the dashboard |
| `ANTHROPIC_API_KEY` | config generation and summaries | **not** gated by `DRY_RUN` |
| `ADMIN_API_KEY` | `/admin` retry and abandon | |

### Optional but recommended for the first test

| Variable | Why |
|---|---|
| `BILLING_GATE_ENABLED=false` | avoids needing a real Stripe subscription. Local/test only — production refuses to boot with it off |
| `EMAIL_PROVIDER=smtp` (keep) | keeps summary emails in Mailpit instead of mailing a real person |
| `ELEVENLABS_VOICE_MAP` | the defaults are real public ElevenLabs voice ids; fine as-is |

### The one thing that costs money even in DRY_RUN

`ANTHROPIC_API_KEY`. The LLM is **deliberately exempt** from `DRY_RUN` —
`effective_llm_provider` ignores it, unlike every other provider. Config
generation and summarization call the real API and bill cents as soon as
`LLM_PROVIDER=anthropic`. That exemption is intentional and documented in
`config.py`, but it is worth knowing it is the one paid call a "dry" run makes.

---

## 4. Which providers to switch from fake to real

**`DRY_RUN` is a global master switch.** While `DRY_RUN=true`, every
`*_PROVIDER` setting is ignored and forced to `fake` (see `effective_*_provider`
in `config.py`). Setting `TWILIO_PROVIDER=twilio` on its own does nothing.

Once `DRY_RUN=false`, each provider becomes individually selectable — which is
what makes a staged rollout possible:

| Provider | Setting | Stage A (safe) | Stage B (live call) |
|---|---|---|---|
| LLM | `LLM_PROVIDER` | `anthropic` | `anthropic` |
| Twilio | `TWILIO_PROVIDER` | `fake` | `twilio` |
| ElevenLabs | `ELEVENLABS_PROVIDER` | `elevenlabs` | `elevenlabs` |
| Email | `EMAIL_PROVIDER` | `smtp` (Mailpit) | `smtp` (Mailpit) |
| Payments | `PAYMENT_PROVIDER` | `fake` | `fake` |
| Object storage | `OBJECT_STORAGE_PROVIDER` | `fake` | `fake` |

**Do Stage A first.** `DRY_RUN=false` with `TWILIO_PROVIDER=fake` exercises real
ElevenLabs agent creation and real LLM config generation while spending nothing
at Twilio. It proves the two integrations most likely to be wrong before any
phone number is touched.

---

## 5. Exact commands to start the application

```bash
# 0. Infrastructure (Postgres, Redis, Temporal, MinIO, Mailpit)
cd "C:/Users/AI TEAM/Desktop/AI_Receptionist"
docker compose up -d

# 1. Migrations
cd backend && uv run alembic upgrade head

# 2. API                                        (terminal 1)
uv run uvicorn app.asgi:app --host 0.0.0.0 --port 8000

# 3. Temporal worker - drives provisioning      (terminal 2)
uv run python -m app.temporal.worker

# 4. Polling worker - post-call summary + email (terminal 3)
uv run python -m app.worker

# 5. Public tunnel for the ElevenLabs webhook   (terminal 4)
ngrok http 8000

# 6. Frontend, optional                         (terminal 5)
cd frontend && npm ci && npm run dev
```

`docker` is not on PATH; prefix with
`export PATH="$PATH:/c/Users/AI TEAM/AppData/Local/Programs/DockerDesktop/resources/bin"`.

**Both workers are required.** The Temporal worker provisions; the polling
worker does post-call processing. Under `ORCHESTRATOR=temporal` the polling
worker deliberately does not claim provisioning runs, so running only one leaves
half the chain dead.

Useful endpoints:

- `curl localhost:8000/healthz` — liveness; `/readyz` — dependency checks
- Temporal UI: http://localhost:8233 — a run's workflow is `provision-{run_id}`
- Mailpit: http://localhost:8025

---

## 6. Signup and test procedure

### Automated: no credentials, no cost. Run this first.

```bash
cd backend
uv run pytest tests/test_end_to_end.py -q   # the full chain against fakes
uv run pytest -q                            # all 551
```

### Manual live test

```bash
# 1. Sign up
curl -X POST http://localhost:8000/api/v1/signups \
  -H 'Content-Type: application/json' \
  -d '{"business_name":"Live Test Salon","business_type":"salon",
       "services":"haircuts, colouring","operating_hours":"Mon-Fri 9-6",
       "greeting_style":"friendly","escalation_rules":"",
       "notification_email":"you@yourdomain.com","area_code":"805",
       "plan":"starter","contact_phone":"8055550123"}'
# Returns tenant_id, run_id and status_token. Provisioning is asynchronous.

# 2. Watch it provision
#    Temporal UI : http://localhost:8233   (workflow provision-<run_id>)
#    or the API  : GET /api/v1/tenants/<tenant_id>/provisioning?token=<status_token>

# 3. Once status is "active", read the number
curl "http://localhost:8000/api/v1/tenants/<tenant_id>/phone?token=<status_token>"

# 4. CALL THAT NUMBER from a real phone. Speak, then hang up.

# 5. ElevenLabs POSTs the transcript to your tunnel. Then:
curl "http://localhost:8000/api/v1/tenants/<tenant_id>/calls?token=<status_token>"
#    Expect: status "notified", a summary, an intent, and your caller id.

# 6. The summary email lands in Mailpit: http://localhost:8025
```

**If step 5 returns nothing:** check the API log for "no tenant owns the called
number" — that is the E.164 mismatch described in section 2.

---

## 7. Real-money and irreversible actions — STOPPING HERE

I have performed **none** of these. Listing them for authorisation:

| # | Action | Trigger | Cost | Reversible? |
|---|---|---|---|---|
| **1** | **Twilio number purchase** | `DRY_RUN=false` + `TWILIO_PROVIDER=twilio`, at the `purchase_number` step | **~$1.15 up front, then ~$1.15/month until released** | Only by releasing it. Compensation releases it automatically if a later step fails |
| 2 | ElevenLabs agent creation | `DRY_RUN=false` + `ELEVENLABS_PROVIDER=elevenlabs` | Free to create; **conversation minutes are billed** | Yes — `delete_agent` |
| 3 | ElevenLabs phone import | same | Free | Yes — `delete_phone_number` |
| 4 | The inbound call itself | dialling the number | Twilio inbound + ElevenLabs conversation minutes | No — it happened |
| 5 | Anthropic LLM calls | `LLM_PROVIDER=anthropic`, **even in DRY_RUN** | Cents per tenant and per call | No |
| 6 | Real outbound email | `EMAIL_PROVIDER=resend` or `sendgrid` | Negligible, but **mails a real person** | No. Keep `smtp`/Mailpit |

### The one that matters, and how to avoid it

**Item 1 is the only meaningful, recurring, hard-to-reverse spend.** It is also
avoidable for the first test.

`purchase_number` checks for an already-owned number before buying
(`find_by_friendly_name`). If a number you **already own** in the Twilio console
carries the tenant's resource name, the step **adopts it and never purchases**:

```
FriendlyName = tenant:<tenant_id>
```

So the zero-purchase live test is:

1. Sign up, note the `tenant_id`. Provisioning will stop at the purchase step —
   that is expected.
2. In the Twilio console, set an existing number's **FriendlyName** to
   `tenant:<tenant_id>`.
3. Retry the run: `POST /api/v1/admin/runs/<run_id>/retry` with the
   `X-Admin-Key` header.
4. The step logs "adopted a number already owned at the vendor" — **$0 spent**.

This gives a genuine end-to-end call on real infrastructure with no new
recurring charge. I recommend it for the first live test.

---

## Recommended sequence

1. **Now, free:** `uv run pytest -q`. Confirms the chain. Done — 551 pass.
2. **Stage A, cents:** `DRY_RUN=false`, `TWILIO_PROVIDER=fake`,
   `ELEVENLABS_PROVIDER=elevenlabs`, `LLM_PROVIDER=anthropic`. Proves real agent
   creation and real config generation. No phone number touched.
3. **Register the webhook** in the ElevenLabs dashboard against your tunnel URL.
4. **Stage B, $0 via adoption:** tag an owned Twilio number,
   `TWILIO_PROVIDER=twilio`, retry the run, then make the call.
5. Only if you want a brand-new number: allow the purchase at step 4.

**I have stopped before step 2.** No credentials entered, no provider switched,
`DRY_RUN` is still `true`, and no vendor call has been made.

---

## Hugging Face: not this project

`~/.cache/huggingface` holds **14.16 GB** of Moshi, Whisper, CSM-1b, Llama-3.2
and Qwen speech models, last written **17 June 2026** — three months before this
repository's first commit (9 September).

Verified three ways:

- `backend/pyproject.toml` declares no `torch`, `transformers`, `huggingface`,
  `tokenizers` or `sentence-transformers`
- none of those packages are installed in `backend/.venv`
- no source file in `backend/app`, `backend/tests`, `frontend/app` or
  `frontend/lib` imports them

**Nothing in this project downloads from Hugging Face.** Voice runs entirely
through the ElevenLabs HTTP API; there is no local model inference anywhere in
the design. The cache is left untouched as instructed — it is unrelated leftover
from earlier local voice-AI experimentation and can be deleted independently of
this project whenever you choose.
