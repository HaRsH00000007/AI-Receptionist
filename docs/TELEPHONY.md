# Telephony architecture

How a phone call reaches a business's AI receptionist, what happens when it
cannot, and why the code is shaped the way it is.

There are two call paths. Both are supported, both are tested, and which one a
deployment uses is a configuration choice (`CALL_PATH`) rather than a rewrite.

---

## 1. The POC path — `CALL_PATH=elevenlabs_native`

```
caller ──▶ Twilio number ──▶ ElevenLabs (owns the number) ──▶ agent
                                        │
                                        ▼
                         POST /api/v1/webhooks/elevenlabs/post-call
```

The number is imported into ElevenLabs, which owns routing end to end. This is
the default, because it works and because existing tenants should not move until
they are migrated deliberately.

What it costs:

* **We see nothing until the call is over.** The first we hear of a call is the
  post-call webhook. A call that never reached ElevenLabs leaves no trace at all.
* **There is no fallback.** If ElevenLabs is down the caller hears whatever the
  vendor does — in practice, silence or a dead line.
* **We cannot route.** One number, one agent, no conditions.

---

## 2. The production path — `CALL_PATH=twiml_stream`

```
caller ──▶ Twilio ──▶ POST /api/v1/voice/inbound          (signature verified)
                             │
                             │  dialled number ──▶ tenant   (Redis, then Postgres)
                             │  tenant + agent  ──▶ decision
                             ▼
                      TwiML response
                             │
              ┌──────────────┼───────────────┬────────────────┐
              ▼              ▼               ▼                ▼
      <Connect><Stream>  <Say><Record>  <Say><Hangup>     <Reject>
              │
              ▼
      ElevenLabs conversational websocket
              │
              ▼
      POST /api/v1/voice/init      ──▶ dynamic variables for THIS tenant
              │
              ▼
        live conversation
              │
              ▼
      POST /api/v1/webhooks/elevenlabs/post-call
```

We own the decision. That buys the fallback, the routing, and a call record at
the moment the call arrives rather than after it ends.

---

## The disposition table

`app/telephony/routing.py` is a pure function over resolved input. It is
separate from the HTTP handler on purpose: this is the one piece of logic a
customer *hears*, every branch is a different experience for a real person on a
phone, and branches buried in a request handler get tested by whichever one the
happy path happens to take.

| Situation | Disposition | What the caller hears |
|---|---|---|
| Active or pending tenant, agent present | `CONNECT_AGENT` | The receptionist |
| Tenant has no agent yet | `VOICEMAIL` | Apology, then a recording tone |
| Voice vendor circuit open | `VOICEMAIL` | "Temporarily unavailable", then a tone |
| Tenant cancelled or abandoned | `UNAVAILABLE` | "No longer in service", then hangup |
| Number resolves to no tenant | `REJECT` | Nothing — the call is not answered |

**The governing rule is that a caller must never hear silence.** Every branch
ends in a working agent, a spoken explanation, or a voicemail. `REJECT` is the
single case where not answering is correct: answering an unowned number costs a
billed minute and confirms to a scanner that the line is live.

Note that `PENDING` connects. A number can be dialled before the welcome email
lands, and a customer's customer should not be turned away over an internal
state they cannot see.

---

## `/voice/init` and the 300ms budget

A shared vertical agent is a generic receptionist until this endpoint tells it
whose call it is. It runs inside the silence before the caller hears anything,
which is why the production plan gives it a hard 300ms p99 budget and why it is
the one endpoint with its own latency histogram.

The read order never varies:

1. **Redis**, with a 250ms socket budget.
2. **PostgreSQL**, which is the source of truth and always correct.
3. **A generic safe configuration**, which still hears the caller and still
   takes a message.

A cache miss and a Redis outage take the identical path, which is what makes the
"Redis is down" test variant meaningful — it is the same code with step 1 always
failing, not a separate branch.

The endpoint never returns an error. The conversation has already started;
failing here is silence on a live call.

---

## Security properties

**Twilio's signature is verified before the dialled number is used.** That
number decides which tenant's agent answers, so acting on an unverified request
would be cross-tenant routing handed to anyone who can reach the URL.

Verification is mandatory whenever an auth token is configured — stricter than
the status callback, which only records what already happened. Only a deployment
with no Twilio credential at all falls back to the environment policy, and a
production-like environment still refuses.

**TwiML is constructed with `xml.etree`, never string formatting.** Business
names come from a signup form; caller ids come from the PSTN. An f-string would
let a `<` in either close a tag and open a `<Dial>`, and Twilio would place a
call to a number of the attacker's choosing and bill it to the tenant. XML
injection here is remote control of a phone call.

**Stream parameters carry identifiers only.** The document reaches Twilio's logs
and the websocket handshake, so a prompt or a token placed there would be
sprayed across two vendors' infrastructure. The far end looks the tenant up by
id through `/voice/init` instead.

**The caller's number is not logged.** It is kept on the call row under the M12
retention policy; a log aggregator has no such policy.

---

## Migrating a tenant to the production path

1. Configure `PUBLIC_API_URL` so signatures verify against the address Twilio
   actually called.
2. Point the number's voice webhook at `POST {PUBLIC_API_URL}/api/v1/voice/inbound`.
3. Set `CALL_PATH=twiml_stream`.
4. Place a test call and confirm the TwiML in Twilio's debugger, then confirm
   the conversation connects.
5. Move tenants in batches. `tenants.agent_mode` and `CALL_PATH` are independent
   — a tenant can be on the production call path with a dedicated agent.

Roll back by pointing the number's webhook back at ElevenLabs. Nothing in the
database changes, so the rollback is a vendor-side configuration edit.

---

## What is not built

* **We do not terminate the media stream ourselves.** Twilio streams directly to
  ElevenLabs. Recording, barge-in and live transfer would need a media server we
  do not have, and inventing one was out of scope.
* **`/voice/init` has not been load-tested at 500 concurrent calls.** The budget
  is measured and the histogram exists; the number in the production plan has
  not been demonstrated.
* **Voicemail recordings are not yet stored or transcribed.** The TwiML takes
  the recording and Twilio holds it; the `recordings` table and its retention
  policy exist, but the callback that files them is not written.
