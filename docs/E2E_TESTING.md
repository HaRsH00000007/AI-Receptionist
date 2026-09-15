# End-to-end testing

How to run the full journey locally, what it proves, and what it deliberately
does not.

---

## Running it

```bash
# 1. Infrastructure. PostgreSQL is the only hard requirement; Redis makes the
#    cache tests run instead of skip.
docker compose up -d
docker compose ps            # wait for "healthy"

# 2. The whole journey, plus its failure scenarios.
cd backend
uv run pytest tests/test_full_e2e.py -v

# 3. Everything.
uv run pytest
```

The suite builds its own scratch database by **running the migrations**, not
`metadata.create_all` — so it tests what a deployment will actually execute, and
a model change nobody wrote a migration for fails here rather than in
production.

It is repeatable from a clean environment by construction: the database is
dropped and recreated per session, and one test provisions two independent
tenants specifically to catch state the first run leaves behind.

---

## What the journey covers

`tests/test_full_e2e.py::test_the_whole_journey` walks, in order:

```
signup                  201, status "draft" — the front door calls no provider
  ↓
billing gate            entitlement read from our subscriptions table
  ↓
LLM config generation   validated against the schema, published as version 1
  ↓
phone number            exactly one, ACTIVE
  ↓
agent creation          points at the live config
  ↓
number linking          and verification
  ↓
tenant ACTIVE
  ↓
inbound call            signed TwiML request → <Connect><Stream> with the agent
  ↓
/voice/init             this tenant's greeting and config version
  ↓
post-call webhook       signed; creates the call record and transcript
  ↓
summary                 LLM-generated, stored on the call
  ↓
notification            queued in the outbox, delivered exactly once
  ↓
usage                   metered once, from the call's real duration
  ↓
dashboard               tenant, phone, calls and usage, via the status grant
  ↓
admin                   the run is visible in the operator console
  ↓
metrics                 the journey is counted, and carries no identifiers
```

---

## The failure scenarios

These carry equal weight. The system this project replaces worked whenever
everything worked; it failed for a week because nobody had tested what happened
when a step did not.

| Scenario | What must happen |
|---|---|
| A call to a number we do not own | `<Reject>` — not answered, not billed |
| The voice vendor is down mid-journey | Voicemail, in milliseconds, not silence |
| The same webhook delivered three times | One call, one usage event, one email |
| A forged post-call webhook | Nothing: no call, no usage, no stored event |
| Another tenant's status grant | 404 on every tenant-scoped path |
| Two tenants provisioned in one process | Independent numbers, each config at v1 |

---

## What it proves

* **Our orchestration is correct.** Every step, its ordering, its idempotency,
  its compensation and its state transitions run against a real database and a
  real HTTP stack.
* **The money-safety invariants hold.** The concurrency tests in
  `tests/test_security_suite.py` race four workers over one provisioning run and
  assert that at most one number is ever live.
* **Tenant isolation holds** across authentication, status grants, the call
  path, the dashboard and the admin surface.
* **The failure paths are real code**, reached by tests rather than asserted in
  a comment.

## What it does not prove

Stated plainly, because an E2E suite that overstates its reach is how a team
becomes confident about something untested.

* **No vendor API shape is verified.** Every provider is a fake. The fakes
  mirror the real interfaces — including Stripe's signature scheme — so the
  application logic is proven and the vendors' actual request/response bodies,
  error codes and edge cases are not. Confirming those needs credentialed
  integration runs.
* **No real audio exists.** The call path is exercised as HTTP and TwiML. No
  media stream is established, so codec negotiation, latency under load and
  barge-in behaviour are untested.
* **No load.** "500 concurrent calls" and the `/voice/init` p99 budget are
  targets in the production plan, not measurements. The histogram to measure
  them exists.
* **Temporal Cloud is not exercised.** The workflows run against the local
  Temporal server and the SDK's time-skipping environment.

---

## Running against real vendors

Deliberately not part of any default command, and deliberately not wired into
CI. `DRY_RUN=true` forces fakes for Twilio, ElevenLabs, email and payments, so
the default suite *cannot* spend money even with live credentials in `.env`.

To exercise a real vendor, set its credential, set `DRY_RUN=false`, and run one
targeted test — knowing it will buy a number, create an agent, send mail or
charge a card. Release anything it creates afterwards.

---

## The skip count is part of the result

```
799 passed in 154s          ← what you want
700 passed, 99 skipped      ← 99 tests did not run
```

A green summary line is not a passing suite. Skips mean PostgreSQL or Redis was
unreachable and whole areas were silently not exercised. CI fails the build if
the database tests skip, so this particular blind spot cannot return quietly.
