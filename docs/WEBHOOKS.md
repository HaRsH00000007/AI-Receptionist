# Webhook setup and security

These endpoints are the only unauthenticated write surface in the application.
Anyone on the internet can POST to them. Everything below is therefore a
security control rather than an integration detail.

---

## The endpoints

| Provider | Path | What it does |
|---|---|---|
| ElevenLabs | `POST /api/v1/webhooks/elevenlabs/post-call` | Creates the call record and transcript |
| Twilio | `POST /api/v1/webhooks/twilio/voice-status` | Records call status for the audit trail |
| Twilio | `POST /api/v1/voice/inbound` | Decides how a live call is answered |
| Stripe | `POST /api/v1/webhooks/stripe` | The **only** way subscription state changes |

---

## Configuration

```bash
# Signed by ElevenLabs over "{timestamp}.{body}", HMAC-SHA256.
ELEVENLABS_WEBHOOK_SECRET=whsec_...

# Twilio signs with the account auth token. No separate secret.
TWILIO_AUTH_TOKEN=...

# Stripe signs over the raw body. Get it from `stripe listen` or the dashboard.
STRIPE_WEBHOOK_SECRET=whsec_...

# REQUIRED behind any proxy, tunnel or load balancer. See below.
PUBLIC_API_URL=https://api.yourdomain.com

# Raises the bar in local/test. Production-like environments always require
# signatures and this cannot lower that.
REQUIRE_WEBHOOK_SIGNATURE=false

# How old a signed delivery may be before it is refused as a replay.
WEBHOOK_TOLERANCE_S=1800
```

### `PUBLIC_API_URL` is not optional behind a proxy

Twilio signs the URL **the caller used**, not the one this process sees. Behind
ngrok or a load balancer the process sees `http://localhost:8000/...` while
Twilio signed `https://api.yourdomain.com/...`, and every signature check fails.

This is the single most common integration failure, and it presents as "all
webhooks rejected" rather than as a configuration error.

Local development with a tunnel:

```bash
ngrok http 8000
# then, in .env:
PUBLIC_API_URL=https://<your-subdomain>.ngrok-free.app
```

---

## The three signature outcomes

The distinction between the second and third is the one that matters.

| Header | Verifies | Behaviour |
|---|---|---|
| Present | Yes | Processed, `signature_valid=true` |
| **Present** | **No** | **Refused everywhere. Nothing stored.** |
| Absent | — | Processed with `signature_valid=false` in local/test; refused in production-like |

A *present and wrong* signature is never benign: it is a forgery attempt, a
replay outside its window, or a rotated secret. Processing it would let anyone
who guessed a customer's phone number inject a transcript that reaches the
summarizer, the customer's inbox and the usage ledger.

The leniency for an *absent* signature exists so a local `DRY_RUN` loop works
with no secret configured. It still records the delivery as unverified, so a
misconfigured production secret is visible in the audit trail rather than
indistinguishable from a verified event.

**Stripe is stricter than both.** It is refused before anything is stored, in
every environment, because it is the only path by which billing state changes —
without verification, an unauthenticated POST could grant any tenant a
subscription and walk straight through the money gate.

---

## Replay protection

Three layers, in order of cost:

1. **The signed timestamp.** ElevenLabs and Stripe sign a timestamp *inside* the
   MAC, so a captured body cannot be refreshed: changing the timestamp
   invalidates the signature, and the attacker cannot recompute it.
2. **Redis dedupe.** A `SETNX` marker short-circuits a redelivery before the
   database is touched. A *positive* answer short-circuits; a miss or an outage
   falls through, never the reverse.
3. **The unique index on `(provider, event_id)`.** The authoritative layer. It
   works with Redis stopped, survives a restart, and cannot be wrong.

Twilio sends no timestamp, so deduplication is the whole replay defence there.
Its event id therefore includes the call *status* as well as the `CallSid` — the
same call legitimately reports `ringing` then `completed`, and collapsing those
would drop a real status change as though it were a redelivery.

---

## Why every response is 200

Including rejections.

A provider that receives a 4xx or 5xx retries. Retrying a payload that can never
succeed — a bad signature, an unparseable body, an unknown tenant — produces a
retry storm and nothing else. So the delivery is recorded (or deliberately not,
for a forgery), a reason is returned, and the provider moves on.

---

## What is stored

Not the payload as sent. `app/services/webhook_payload.py` redacts before the
row is written:

* **Credentials** are matched by key *pattern* (`secret`, `token`, `api_key`,
  `authorization`, …) so a field a provider adds later is caught by the pattern
  rather than by someone noticing.
* **Transcripts and conversational content** are replaced with a summary of what
  was dropped. The transcript belongs on `calls`, under one retention policy,
  once — a copy here would be PII in a second place, outliving the policy that
  governs it.
* **Long strings** are truncated and **deep nesting** is cut off, because an
  unbounded walk over an attacker-supplied document is a denial of service.

The admin panel renders these rows, which is the other reason to keep as little
attacker-influenced content in them as possible.

---

## Testing a webhook locally

```bash
# Stripe, with the CLI forwarding real test-mode events:
stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe

# ElevenLabs and Twilio: the suite builds valid signatures for you.
cd backend && uv run pytest tests/test_webhook_security.py -v
```

To hand-craft a signed ElevenLabs delivery:

```python
from app.services.signatures import build_elevenlabs_signature
import json, time

body = json.dumps({"conversation_id": "conv_1"}).encode()
header = build_elevenlabs_signature(
    payload=body, secret="your-secret", timestamp=int(time.time())
)
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Every Twilio delivery rejected | `PUBLIC_API_URL` unset or wrong behind a proxy |
| Every ElevenLabs delivery rejected | Secret mismatch, or clock skew beyond `WEBHOOK_TOLERANCE_S` |
| Deliveries accepted but `signature_valid=false` | No secret configured — fine locally, a bug in production |
| Stripe rejected with a correct secret | The body was re-serialized somewhere; the MAC covers the raw bytes |
| A call status change appears to be "duplicate" | Expected only if the *same* status is redelivered |
