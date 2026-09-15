# Operations and recovery

What to do when something is wrong, and what the system does on its own before
anyone looks.

The organizing principle: **the system this replaces failed silently for a
week.** Everything below is arranged so that a failure is visible, bounded, and
recoverable by one person with a browser.

---

## 1. First look

```bash
curl -s localhost:8000/healthz   # is the process alive?
curl -s localhost:8000/readyz    # can it serve traffic?
curl -s localhost:8000/metrics   # what is it doing?
```

`/readyz` distinguishes two things that look alike:

```json
{
  "status": "ready",
  "checks": {
    "database": {"ok": true,  "critical": true},
    "redis":    {"ok": false, "critical": false}
  },
  "degraded": ["redis"]
}
```

`status: ready` with a non-empty `degraded` means **serving, but something is
wrong**. Only a *critical* dependency withdraws the replica — a cache outage
degrades latency, never correctness, and a replica that withdrew itself over one
would convert a slow service into no service.

---

## 2. The metrics that answer questions

| Question | Metric |
|---|---|
| Is provisioning succeeding? | `provisioning_runs_total{outcome=...}` |
| Which step is failing? | `provisioning_steps_total{step,outcome}` |
| Which vendor is down? | `provider_calls_total{provider,outcome}` |
| Are webhooks being rejected? | `webhook_deliveries_total{provider,status}` |
| Are callers reaching an agent? | `inbound_calls_total{disposition}` |
| Is the audible path inside budget? | `voice_init_duration_seconds` |
| Are notifications going out? | `notifications_total{outcome}` |

A rising `inbound_calls_total{disposition="voicemail"}` is the alert that
matters most: callers are reaching a business and not reaching its receptionist.

Labels carry no identifiers. A tenant id would be an unbounded cardinality
explosion; correlate through logs and the `correlation_id` instead.

---

## 3. A provisioning run failed

Nothing is lost. A failed run is durable, visible and retryable.

1. Open `/admin`, filter to **Failed**.
2. Read `last_error` — it is the vendor's own message, classified.
3. **Retry.** Every step is idempotent and guarded by adoption checks, so a
   retry adopts a half-created resource rather than buying a second one. It is
   safe to press twice.
4. If retrying cannot work — the area code has no numbers, the tenant is not
   coming back — **Abandon**. That runs compensation: any purchased number is
   released, the run is marked compensated, and the money stops.

### A run parked in `BILLING_BLOCKED`

Not a failure. The tenant is not entitled yet and nothing is broken. It resumes
automatically when Stripe says so, and re-checks itself on
`BILLING_RECHECK_INTERVAL_S` in case a webhook is never delivered. No action is
needed unless the customer says they have paid and it has not moved — in which
case check `subscriptions` for that tenant, because entitlement is read from our
table and never from Stripe.

### A run stuck in `DRAFT` with no workflow

Signup starts the workflow best-effort so a Temporal outage cannot break the
front door. If Temporal was down at that moment, the run sits in `DRAFT` with no
execution. **Retry** from the admin panel starts it. There is no reconciler
sweeping for these; see "Known gaps".

---

## 4. A vendor is down

The circuit breaker notices after `CIRCUIT_FAILURE_THRESHOLD` consecutive
failures and stops attempting calls for `CIRCUIT_COOLDOWN_S`, then admits one
probe.

**What callers experience during an ElevenLabs outage:** a recorded apology and
a voicemail, answered in milliseconds rather than after a websocket timeout.
Degraded, not lost — the business still gets the message.

**During a Twilio outage:** provisioning stalls with retries; existing calls are
unaffected because Twilio is the thing carrying them.

**During an LLM outage:** config generation falls back to the deterministic
template, which is less tailored and never wrong. Call summarization retries on
its own durable schedule.

**During a Redis outage:** everything works, more slowly. Every cache read has a
PostgreSQL fallback, rate limiting degrades to per-replica, and locks fail open
to the database constraints that were the real guarantee anyway.

**During a Temporal outage:** the API stays up, signup still records tenants,
status pages still answer. Only the *starting* of new workflows degrades. The
polling engine remains behind `ORCHESTRATOR=state_machine` as a documented
fallback — but never run both at once, because two orchestrators over the same
runs could both buy a number.

---

## 5. A bad prompt is live

Roll back the configuration version. It is a flag move in PostgreSQL, it takes
no vendor call, and it works while ElevenLabs is unreachable.

```bash
# See what this tenant has had.
curl -H "x-admin-key: $ADMIN_API_KEY" \
  localhost:8000/api/v1/admin/tenants/$TENANT_ID/configs

# Make an earlier version live again.
curl -X POST -H "x-admin-key: $ADMIN_API_KEY" \
  localhost:8000/api/v1/admin/tenants/$TENANT_ID/configs/3/rollback
```

The vendor is now out of date. Push the restored prompt with `resync-agent` —
deliberately a separate, retryable action, so the rollback itself never depends
on the vendor being up.

`resync-agent` **refuses to run against a shared vertical agent**, because
pushing one tenant's prompt would overwrite the prompt every other tenant on
that vertical is served by.

---

## 6. Data deletion request

```sql
INSERT INTO data_deletion_requests (id, tenant_id, scope, status)
VALUES (gen_random_uuid(), '<tenant-uuid>', 'all', 'pending');
```

The worker executes it and writes the counts back onto the row, which is the
receipt. The request row is deliberately retained after completion — a deletion
that erased its own record could not prove it happened.

Billing events, subscriptions, usage events and audit logs are **never** erased.
They are the records needed to answer the dispute the deletion request may
itself be part of.

---

## 7. Routine maintenance

The worker runs these itself every `MAINTENANCE_INTERVAL_S`, so nothing depends
on someone remembering to deploy a scheduler:

* **Retention sweep** — redacts transcripts past `TRANSCRIPT_RETENTION_DAYS` and
  marks recordings past `RECORDING_RETENTION_DAYS`. Batched and idempotent.
* **Usage rollups** — rebuilds the last three days from the ledger. Recomputed,
  never incremented, so a late webhook is harmless and overlapping runs cannot
  double-count.

Both are logged and audited. Both swallow their own failures, because a
retention problem must not stop the worker summarizing calls and sending mail.

---

## 8. Backups and restore

PostgreSQL is the source of truth; everything else is reconstructible.

* Redis — a cache. Losing it costs latency.
* Temporal — orchestration. `provisioning_runs` is the read model the API uses.
* ElevenLabs and Twilio — projections of our tables.
* MinIO/S3 — recordings only, governed by their own retention.

**A backup you have not restored is not a backup.** The restore drill is not
automated here; it is listed under "Known gaps".

---

## 9. Known gaps

Stated plainly because an operations document that overstates its coverage is
worse than none.

* **No reconciler for `DRAFT` runs with no workflow.** Needs an operator click.
* **No alerting.** Metrics exist and are correct; nothing pages anyone. Wire
  `/metrics` to a scraper and alert on provisioning failure rate,
  `inbound_calls_total{disposition="voicemail"}`, and webhook rejection rate.
* **No restore drill.** PITR is configured in the AWS mapping, never exercised.
* **Temporal Cloud has never been connected.** The mTLS path is written and
  type-checked only.
* **The real Stripe adapter has never run against Stripe.** Every test uses the
  fake, which mirrors the signature scheme exactly — so the application logic is
  proven and Stripe's live API shape is not.
* **`/voice/init` has not been load-tested** at the 500-concurrent-call target.
