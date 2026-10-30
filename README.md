<div align="center">

# Evenline — AI Receptionist

**An AI receptionist for small businesses: a business signs up, picks a local number, and within a few minutes every call to that number is answered in the business's own voice, then summarised and sent to the owner.**

<img src="docs/images/landing.png" alt="Evenline landing page" width="100%">

[What's new](#whats-new) · [Features](#features) · [Screenshots](#screenshots) · [System design](#system-design) · [Getting started](#getting-started) · [Testing](#testing) · [Known limitations](#known-limitations)

</div>

---

## What it does

```
sign up
   │
   ├─ "use my website"  ─►  read the site  ─►  review what it learned
   └─ "set up manually" ─►  fill in the form
   │
   ▼
choose a number ─►  provisioning (8 durable steps) ─►  receptionist is LIVE


caller dials ─►  Twilio ─►  ElevenLabs voice agent ─►  post-call webhook ─►  AI summary
                                                                                │
                                                                                ▼
                    owner gets an email · it appears in the dashboard ·
                    the caller is saved as a contact
```

A business owner either hands over their website or fills in one guided form.
Both paths end in the same place, and **neither buys anything until the owner
says so**.

The API validates and saves the signup in milliseconds, then starts a durable
provisioning workflow. That workflow writes the receptionist's configuration,
checks the plan is entitled, buys the phone number the owner chose, creates a
voice agent, connects the two and verifies the result. Every step is recorded,
retryable and visible in the setup screen, so nothing can fail silently.

The platform replaces a Make.com automation chain that had exactly that problem:
a multi-step process with irreversible external side effects, no durable state,
no idempotency and no visibility.

---

## What's new

Three features landed in this round of work. The existing manual signup path is
untouched — it still renders from the original prompt template, so a receptionist
created before any of this answers exactly as it did. The one behaviour that did
change for existing customers is the contacts page, which is
[noted below](#contacts-1).

### 1. Set up from your website

Paste a URL instead of filling in a form. The platform reads a handful of the
site's own pages, turns them into a list of facts the receptionist may share,
and **shows the owner every one of them before anything goes live**. Items can
be edited, switched off or added by hand.

What makes this safe to point at the open internet:

| Concern | How it is handled |
|---|---|
| **SSRF** | `app/website/guard.py` resolves DNS itself and refuses loopback, private, link-local and metadata addresses, credentials in the URL, dotless hosts, `.internal` names and any port outside 80/443/8080/8443. A hostname that resolves to a mix of public and private addresses is refused outright. |
| **DNS rebinding** | Connections are pinned to the exact address that was vetted (`PinnedTransport`), with SNI preserved. Redirects do not follow automatically — each hop is re-validated. |
| **Runaway crawls** | Hard budgets: 12 pages, depth 2, 2 MB per page, 12 MB and 45 seconds overall, 4 at a time. `robots.txt` is honoured. |
| **Prompt injection from the page** | Page text reaches the model fenced as untrusted content, the reply is schema-validated, and any "fact" whose wording is not supported by the crawled text is dropped rather than shown. |
| **Spending money** | Confirming the draft reuses the *existing* signup service. There is no second code path to a purchase, and the number is still bought by the same provisioning step behind the same billing gate. |

### 2. Contacts

Everyone who has called, plus anyone the business adds. Contacts are now real
records rather than a view derived from the call history, so a name typed by the
owner survives, and a contact can exist before anyone has called.

- CSV **import and export** — import reports every row it could not read, with
  spreadsheet row numbers; export prefixes `=`, `+`, `-` and `@` so a cell can
  never execute as a formula.
- **Blocking marks and filters a contact; it does not refuse their calls.**
  Refusing a caller is a decision with consequences a business should make
  deliberately, so the product does not make it on their behalf. The UI says so.
- A name the owner typed is **never** overwritten by one a caller claimed.

### 3. Delete a receptionist

Owners can stop and remove a receptionist themselves, and choose whether to
hand the phone number back. The tenant row and the call history are always
kept; a shared vertical agent is detached, never deleted. If a vendor cleanup
step fails the response says what is still outstanding and a retry finishes the
job.

<table>
  <tr>
    <td width="50%"><img src="docs/images/setup-choice.png" alt="Choosing between website setup and manual setup"><br><sub><b>Two ways in.</b> Nothing goes live until the owner says so.</sub></td>
    <td width="50%"><img src="docs/images/contacts.png" alt="Contacts"><br><sub><b>Contacts</b>, with search, Active/Blocked tabs and per-contact call counts.</sub></td>
  </tr>
</table>

---

## Features

### For business owners

| | |
|---|---|
| **Set up from your website** 🆕 | Hand over a URL and the platform reads the site, then shows you every fact it learned so you can correct it. Eight steps, resumable, and nothing reaches a caller unreviewed. |
| **Contacts** 🆕 | Everyone who has called, plus anyone you add. Search, notes, blocking, and CSV import and export. |
| **Delete your receptionist** 🆕 | Stop and remove it yourself, and choose whether to keep the number. Your call history stays. |
| **Guided onboarding** | Five steps: business, receptionist, phone, review and activate. A live preview shows exactly what callers will hear. |
| **Custom greetings** 🆕 | Three ways to open a call: have one written from your details, pick a ready-made line in your chosen style, or write your own wording. What you preview is what callers hear. |
| **Choose your own number** 🆕 | Search the numbers actually available in your area code and pick one. If that area code is sold out, **nearby numbers in the same state** are offered instead, never just the next code up numerically. Your pick is bought as chosen, and if someone else takes it first, setup finds the closest match rather than failing. |
| **Keep your existing number** | Get a new AI line and forward your current number to it, with step-by-step forwarding instructions. |
| **Password sign-in** 🆕 | Choose a password during signup and sign straight in to the dashboard, no email needed. A one-time email sign-in link is still available as a fallback. |
| **Live setup tracking** | Watch all eight provisioning steps complete in real time. You can close the page and setup keeps running. |
| **Call summaries** | Every call is summarised by an LLM: who called, why, their callback number, the intent and how urgent it is. Summaries appear in the dashboard and are emailed to you. |
| **Dashboard** | Overview, calls with search and filters, call details, receptionist and configuration, phone numbers, usage, billing and settings, in light and dark themes. |
| **Escalation rules** | Tell the receptionist in plain English when a call is urgent or when you want to be involved. |

### For the operator

| | |
|---|---|
| **Operator console** | Every tenant's provisioning run, with status, current step and last error. Retry or abandon a run, and roll back or resync a receptionist's configuration. |
| **System health** | Live readiness of the database, Redis and other dependencies. |
| **Billing gate** | A number, which costs money, is only bought after the plan's entitlement is re-checked immediately before the purchase. |
| **Usage metering** | Call minutes are tracked against the plan's allowance, with a warning at 80%. |
| **Audit log** | Sign-ins, configuration changes, purchases and impersonation are all recorded. |

---

## Screenshots

> These are from a local demo run with **every vendor faked** (`DRY_RUN=true`)
> and sample call data. No real numbers were bought and no real calls placed.
> The signed-in address is shown as `owner@example.com`; the website in the
> setup screenshots is a placeholder domain, and that draft was seeded locally
> rather than crawled from anyone's site.

### Setting up from a website

<table>
  <tr>
    <td width="50%"><img src="docs/images/website-url.png" alt="Step 2: enter your website"><br><sub><b>Step 2 — Your website.</b> A public page, read without signing in, honouring <code>robots.txt</code>.</sub></td>
    <td width="50%"><img src="docs/images/website-knowledge.png" alt="Step 4: review what the agent learned"><br><sub><b>Step 4 — Knowledge.</b> Every extracted fact, with its own switch. "Check this" marks anything the crawl was unsure of.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/website-questions.png" alt="Step 5: what the agent asks callers"><br><sub><b>Step 5 — Questions.</b> What the receptionist asks a caller. Name, email and phone are built in and can be switched off, not renamed.</sub></td>
    <td width="50%"><img src="docs/images/website-voice.png" alt="Step 6: choose a voice"><br><sub><b>Step 6 — Voice.</b> The voice vendor’s own catalogue, read live — its names, its one-line descriptions and its samples, so you hear a voice before you choose it. A handful up front, the rest one click away. English is the only language the call path supports, and it says so.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/website-greeting.png" alt="Step 7: how the agent introduces itself"><br><sub><b>Step 7 — Greeting.</b> Callers hear this word for word.</sub></td>
    <td width="50%"><img src="docs/images/website-number.png" alt="Step 8: pick a number and create"><br><sub><b>Step 8 — Number.</b> Everything the receptionist will start with. <b>Nothing has been bought up to this point.</b></sub></td>
  </tr>
</table>

### Contacts

<img src="docs/images/contacts.png" alt="Contacts" width="100%">

<table>
  <tr>
    <td width="50%"><img src="docs/images/contacts-import.png" alt="Importing contacts from a CSV"><br><sub><b>Import.</b> An import fills in blanks and never overwrites what the owner typed. Unreadable rows are listed back.</sub></td>
    <td width="50%"><img src="docs/images/agent-delete-confirm.png" alt="Deleting a receptionist"><br><sub><b>Deleting a receptionist.</b> Releasing the number is a separate, explicit choice, with its consequence spelled out.</sub></td>
  </tr>
</table>

### Onboarding

<table>
  <tr>
    <td width="50%"><img src="docs/images/signup-business.png" alt="Step 1: business details"><br><sub><b>Step 1 — Business details.</b> The preview on the right updates as you type.</sub></td>
    <td width="50%"><img src="docs/images/signup-greeting.png" alt="Step 2: greeting, hours and password"><br><sub><b>Step 2 — Receptionist.</b> Greeting style, opening line (written for you, ready-made, or your own), hours, escalation rules and the account password.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/signup-number-exact.png" alt="Numbers available in the requested area code"><br><sub><b>Step 3 — Choose a number.</b> Real inventory in the requested area code (415).</sub></td>
    <td width="50%"><img src="docs/images/signup-number-nearby.png" alt="Nearby numbers offered when the area code is empty"><br><sub><b>Nothing in 916?</b> Nearby California numbers are offered, and only when the requested code has none.</sub></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><img src="docs/images/signup-review.png" alt="Review and activate" width="70%"><br><sub><b>Step 4 — Review and activate.</b> Everything that's about to be set up, in plain words. The password is never shown back.</sub></td>
  </tr>
</table>

### Provisioning

<table>
  <tr>
    <td width="50%"><img src="docs/images/provisioning-running.png" alt="Provisioning in progress"><br><sub>Each of the eight steps is durable and retryable, and is shown as it happens.</sub></td>
    <td width="50%"><img src="docs/images/provisioning-done.png" alt="Provisioning complete"><br><sub>Live, with the number the customer chose.</sub></td>
  </tr>
</table>

### Customer dashboard

<img src="docs/images/dashboard.png" alt="Dashboard overview" width="100%">

<table>
  <tr>
    <td width="50%"><img src="docs/images/calls.png" alt="Calls list"><br><sub><b>Calls</b>, with search, date and status filters, and urgent calls flagged.</sub></td>
    <td width="50%"><img src="docs/images/call-detail.png" alt="Call detail"><br><sub><b>Call detail</b>: the AI summary, the callback number the caller gave, intent and urgency.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/receptionist.png" alt="AI receptionist page"><br><sub><b>AI Receptionist</b>: its number, voice, greeting, hours and escalation rules.</sub></td>
    <td width="50%"><img src="docs/images/configuration.png" alt="Configuration"><br><sub><b>Configuration</b>: exactly what the receptionist works from.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/phone.png" alt="Phone numbers"><br><sub><b>Phone numbers</b>, with call-forwarding setup.</sub></td>
    <td width="50%"><img src="docs/images/usage.png" alt="Usage"><br><sub><b>Usage</b>: minutes against the plan's allowance.</sub></td>
  </tr>
</table>

### Sign in and operator console

<table>
  <tr>
    <td width="50%"><img src="docs/images/login.png" alt="Sign in"><br><sub><b>Sign in</b> with a password, or ask for an emailed link.</sub></td>
    <td width="50%"><img src="docs/images/admin.png" alt="Operator console"><br><sub><b>Operator console</b>: every provisioning run, with retry, abandon and system health.</sub></td>
  </tr>
</table>

### Dark mode

<table>
  <tr>
    <td width="50%"><img src="docs/images/landing-dark.png" alt="Landing page, dark"></td>
    <td width="50%"><img src="docs/images/dashboard-dark.png" alt="Dashboard, dark"></td>
  </tr>
</table>

---

## System design

### Architecture

```mermaid
flowchart LR
    subgraph Browser
        WEB["Next.js 16 app<br/>marketing · onboarding<br/>dashboard · operator console"]
    end

    subgraph API["FastAPI"]
        REST["REST API /api/v1<br/>signups · numbers · auth<br/>tenants · admin"]
        HOOKS["Webhooks<br/>ElevenLabs · Twilio · Stripe"]
    end

    subgraph Workers["Background workers"]
        TW["Temporal worker<br/>provisioning workflow"]
        PW["Polling worker<br/>post-call · email outbox<br/>website reading · retention<br/>usage rollups"]
    end

    subgraph State
        PG[("PostgreSQL 16<br/>source of truth")]
        RD[("Redis<br/>locks · rate limits<br/>dedupe · hot cache")]
        TMP["Temporal server"]
    end

    subgraph Vendors["External providers"]
        TWILIO["Twilio<br/>numbers · voice"]
        EL["ElevenLabs<br/>voice agent"]
        LLM["LLM<br/>Anthropic · OpenAI · Groq"]
        MAIL["Email<br/>Resend · SendGrid · SMTP"]
        STRIPE["Stripe<br/>billing"]
        SITE["The customer's<br/>own website"]
    end

    CALLER(["Caller"])

    WEB -- "HTTPS + session cookie" --> REST
    REST --> PG
    REST --> RD
    REST -- "start workflow" --> TMP
    TMP <--> TW
    TW --> PG
    TW --> TWILIO
    TW --> EL
    TW --> LLM
    TW --> STRIPE
    PW --> PG
    PW --> LLM
    PW --> MAIL
    PW -- "read, through the SSRF guard" --> SITE
    CALLER -- "dials the number" --> TWILIO
    TWILIO --> EL
    EL -- "post-call transcript" --> HOOKS
    STRIPE -- "subscription events" --> HOOKS
    HOOKS --> PG
```

**Ground rules:**

1. **PostgreSQL is the source of truth.** No provisioning state lives only in
   memory, Redis or Temporal. Vendors hold *copies* of what the database says.
2. **Deterministic code handles money, infrastructure and state.** LLMs handle
   language only. They never choose a number, retry a step, activate a tenant or
   pick a voice.
3. **Every external side effect is idempotent.** A crashed or restarted worker
   can't buy a second number, create a duplicate agent or resend an email.
4. **Every vendor sits behind a `Protocol` with a stateful fake,** so the whole
   platform runs in tests with no network and no cost.
5. **`DRY_RUN=true` is the default,** and it forces the fakes for everything
   that spends money.

### Provisioning state machine

Eight steps, each durable and individually retryable. `billing_gate` sits
immediately before `purchase_number`, the only step that spends money, and
entitlement is checked again inside that step just before the purchase.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> draft
    draft --> validated: validate
    validated --> config_generated: generate_config
    config_generated --> billing_authorized: billing_gate
    config_generated --> billing_blocked: not entitled yet
    billing_blocked --> billing_authorized: plan becomes active
    billing_authorized --> number_purchased: purchase_number
    number_purchased --> agent_created: create_agent
    agent_created --> number_linked: link_number
    number_linked --> verified: verify
    verified --> active: activate
    active --> [*]

    validated --> failed: terminal error
    number_purchased --> failed
    agent_created --> failed
    number_linked --> failed
    failed --> compensating: release what was bought
    compensating --> compensated
    compensated --> [*]
```

Retryable errors (timeouts, rate limits, 5xx) back off and try again. Terminal
errors fail the run, and **compensation** releases the number and deletes the
agent so nothing keeps billing. A step that finds its work already done, such
as a number already tagged to this tenant at Twilio, **adopts** it instead of
repeating it.

### Setting up from a website

The crawl and the LLM call both happen in a **leased background job**
(`FOR UPDATE SKIP LOCKED`, three attempts), not in the request, so the owner can
close the tab. The wizard polls the draft.

```mermaid
sequenceDiagram
    autonumber
    actor Owner
    participant Web as Next.js wizard
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Polling worker
    participant Guard as SSRF guard
    participant Site as The owner's website
    participant LLM

    Owner->>Web: pastes example.com
    Web->>API: POST /website-setup
    API->>Guard: is this host allowed at all?
    Guard-->>API: resolved public addresses
    API->>DB: draft (pending)
    API-->>Web: 202
    W->>DB: claim the draft
    W->>Guard: re-resolve and pin each address
    W->>Site: fetch, pinned, robots-aware, budgeted
    Site-->>W: a handful of pages
    W->>LLM: page text, fenced as UNTRUSTED
    LLM-->>W: candidate facts
    Note over W: schema-checked; anything the<br/>pages do not support is dropped
    W->>DB: draft (ready)
    Web->>API: GET /website-setup (polling)
    Owner->>Web: edits, switches items off, picks a voice
    Owner->>Web: "Create my receptionist"
    Web->>API: POST /website-setup/confirm
    API->>API: build the manual form's own request
    Note over API: reuses SignupService — the number<br/>is still bought by the normal<br/>provisioning step, behind the<br/>normal billing gate
```

Two rules this design exists to keep:

1. **Nothing a website says becomes an instruction.** Page text is data. It is
   fenced in the prompt, the reply is parsed against a schema, and a claim the
   crawled text does not support never reaches the review screen.
2. **There is one path to a purchase.** Confirming a website draft assembles the
   same request the manual form produces and hands it to the same service. A
   second way to spend money is a second way to spend it twice.

### Signup and choosing a number

```mermaid
sequenceDiagram
    autonumber
    actor Owner
    participant Web as Next.js
    participant API as FastAPI
    participant Twilio
    participant DB as PostgreSQL
    participant WF as Temporal workflow

    Owner->>Web: enters area code 916
    Web->>API: GET /numbers/available?area_code=916
    API->>Twilio: search 916
    Twilio-->>API: none available
    Note over API: Only now: look up the state for 916 (CA)
    API->>Twilio: search in California
    Twilio-->>API: 213, 310, 415, ...
    API-->>Web: exact_match=false, nearby numbers
    Owner->>Web: picks +1 (213) 555-1000, sets a password
    Web->>API: POST /signups
    API->>DB: tenant + profile + owner account + membership + run + 8 steps, one transaction
    API->>WF: start provisioning
    API-->>Web: 201 + status token
    WF->>Twilio: buy +1 (213) 555-1000
    alt number was sold in the meantime
        WF->>Twilio: fall back to the search ladder
    end
    WF->>DB: phone number ACTIVE
```

Nothing is reserved between the search and the purchase. Losing that race
costs the customer a different number, not a failed signup.

### A call, end to end

```mermaid
sequenceDiagram
    autonumber
    actor Caller
    participant Twilio
    participant EL as ElevenLabs agent
    participant API as FastAPI webhooks
    participant DB as PostgreSQL
    participant W as Polling worker
    participant LLM
    participant Mail as Email
    actor Owner

    Caller->>Twilio: dials the business number
    Twilio->>EL: routes the call
    EL->>Caller: greets with the business's own opening line
    EL->>API: post-call webhook (transcript)
    API->>API: verify signature, dedupe
    API->>DB: store call (received)
    W->>DB: claim call
    W->>LLM: summarise the transcript
    W->>DB: summary, caller, callback number, intent, urgency
    W->>DB: meter usage, queue notification (outbox)
    W->>Mail: send the summary
    Owner->>DB: sees it in the dashboard
```

### Authentication

```mermaid
flowchart TB
    SIGNUP["Signup form<br/>email + password"] --> ACCT["Owner account<br/>Argon2id password hash<br/>+ owner membership"]
    ACCT --> PW["POST /auth/password-session"]
    ACCT --> ML["POST /auth/magic-link<br/>emailed, single-use, 15 min"]
    ML --> EX["POST /auth/session<br/>exchange the link"]
    PW --> SESS["Session<br/>HttpOnly · Secure · SameSite=Lax cookie"]
    EX --> SESS
    SESS --> AUTHZ["Every request<br/>session → membership → role"]
    AUTHZ --> DASH["Dashboard<br/>own tenant only"]
```

- **Two ways in, one session.** Both routes produce the same session row, so
  what a session can do never depends on how it was created.
- **No account enumeration.** A wrong password, an unknown address, a
  link-only account and a disabled account all get the same 401. The
  password check takes the same time even when there's no account.
- **No takeover through signup.** Signing up with an email that already has an
  account never changes that account's password.
- **Only hashes are stored:** Argon2id for passwords, SHA-256 for session and
  link tokens.

### Data model

```mermaid
erDiagram
    TENANTS ||--|| BUSINESS_PROFILES : "is described by"
    TENANTS ||--o{ MEMBERSHIPS : has
    USERS ||--o{ MEMBERSHIPS : has
    USERS ||--o{ SESSIONS : "signs in with"
    TENANTS ||--o{ PROVISIONING_RUNS : "is set up by"
    PROVISIONING_RUNS ||--|{ PROVISIONING_STEPS : contains
    TENANTS ||--o{ PHONE_NUMBERS : owns
    TENANTS ||--o{ AGENT_CONFIGS : "versions of"
    TENANTS ||--o| AGENTS : "answered by"
    TENANTS ||--o{ CALLS : receives
    TENANTS ||--o{ CONTACTS : "keeps on file"
    USERS ||--o| WEBSITE_ONBOARDINGS : "may be setting up from a site"
    CALLS ||--o{ USAGE_EVENTS : meters
    CALLS ||--o{ RECORDINGS : "may have"
    TENANTS ||--o{ SUBSCRIPTIONS : "billed by"
    TENANTS ||--o{ NOTIFICATIONS : sends
    NOTIFICATIONS ||--o{ NOTIFICATION_ATTEMPTS : "tried by"
    TENANTS ||--o{ AUDIT_LOGS : "recorded in"

    TENANTS {
        uuid id
        string status
        string area_code
        string requested_number "the number the owner picked"
    }
    USERS {
        uuid id
        string email
        string password_hash "Argon2id, nullable"
    }
    BUSINESS_PROFILES {
        string greeting_style
        string greeting_custom "the owner's own opening line"
    }
    CALLS {
        string summary
        string intent
        int urgency
    }
    CONTACTS {
        string phone_e164 "unique per tenant"
        string full_name "nullable: unknown callers exist"
        string status "active or blocked"
        string source "manual, call or import"
    }
    WEBSITE_ONBOARDINGS {
        string website_url
        string status "pending, crawling, generating, ready, confirmed"
        json knowledge_json "the draft the owner edits"
    }
```

A contact's **call count is never stored** — it is read from `calls` — so the
two can't disagree. Deleting a contact tidies the address book and leaves the
call history alone.

Supporting tables: `webhook_events` (dedupe), `idempotency_keys`,
`billing_events`, `usage_daily`, `data_deletion_requests`.

---

## Tech stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 16 (App Router), React 19, TypeScript (strict), Tailwind CSS v4, Vitest + Testing Library |
| **API** | FastAPI, Pydantic v2, Python 3.12, uv |
| **Database** | PostgreSQL 16, SQLAlchemy 2.0 (async, asyncpg), Alembic |
| **Orchestration** | Temporal (provisioning) and a polling worker (post-call, outbox, maintenance) |
| **Cache and locks** | Redis 7, always with a PostgreSQL fallback |
| **Voice** | Twilio (numbers), ElevenLabs (conversational agent) |
| **LLM** | Anthropic, OpenAI or Groq, behind one interface |
| **Website reading** | httpx with a pinned transport, BeautifulSoup for extraction, `robots.txt` honoured |
| **Email** | Resend, SendGrid or SMTP, delivered through a durable outbox |
| **Billing** | Stripe |
| **Auth** | Argon2id passwords, magic links, HttpOnly session cookies, role-based memberships |
| **Local stack** | Docker Compose: PostgreSQL, Redis, Temporal + UI, MinIO, Mailpit |

---

## Project structure

```
backend/
  app/
    api/v1/            signups · numbers · auth · tenants · admin · webhooks · voice
                       website (website-based setup) · contacts
    core/              config, logging, correlation ids, error taxonomy, metrics
    data/              area code → state → timezone (no LLM involved)
    models/            SQLAlchemy models and the state-machine enums
    providers/
      protocols.py     one interface per vendor
      fakes/           stateful in-memory vendors with failure injection
      real/            httpx adapters: Twilio, ElevenLabs, LLMs, email, Stripe
    provisioning/      the step engine, retry policy, compensation, one module per step
    temporal/          workflow, activities, worker
    website/           guard.py (SSRF boundary) · crawler.py (pinned, budgeted)
                       knowledge.py (the LLM step, schema-checked)
    services/          signup, auth, passwords, number search, config generation,
                       summaries, billing gate, notifications, usage, retention,
                       website_onboarding, contacts, receptionist_deletion
    worker.py          the polling worker (python -m app.worker)
  migrations/          Alembic
  tests/               1,074 tests against real PostgreSQL
frontend/
  app/                 routes: (marketing), (auth), (onboarding), dashboard, admin, status
  components/          ui/ · app/ · onboarding/ · auth/ · marketing/ · admin/
  lib/                 typed API client, types, formatting, brand
  tests/               188 tests
docs/                  decisions, plans, operations, telephony, webhooks, screenshots
  gtm/                 go-to-market and cost management
```

---

## Getting started

**Requirements:** Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+,
and Docker.

```bash
# 1. Configuration: .env.example documents every variable. Never commit .env.
cp .env.example .env

# 2. Infrastructure: PostgreSQL, Redis, Temporal, MinIO, Mailpit
docker compose up -d

# 3. Backend
cd backend
uv sync
uv run alembic upgrade head

# 4. Frontend
cd ../frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local
```

### Running it

```bash
# terminal 1: API
cd backend && uv run uvicorn app.asgi:app --reload --port 8000

# terminal 2: Temporal worker, which drives provisioning
cd backend && uv run python -m app.temporal.worker

# terminal 3: polling worker (post-call processing, email outbox, maintenance)
cd backend && uv run python -m app.worker

# terminal 4: frontend
cd frontend && npm run dev
```

Open <http://localhost:3000>. The API docs are at <http://localhost:8000/docs>,
and health checks at `/healthz` (liveness) and `/readyz` (readiness).

> **Without Temporal:** set `ORCHESTRATOR=state_machine` and the polling worker
> drives provisioning itself, so terminal 2 isn't needed.

> **Why port 55432 for PostgreSQL?** A native PostgreSQL install usually already
> holds 5432. Docker still reports the port as published, but connections reach
> the other server and fail with a confusing `password authentication failed`.

### Key environment variables

| Variable | Default | Why it matters |
|---|---|---|
| `DRY_RUN` | `true` | Forces fake Twilio, ElevenLabs, email and Stripe. **Nothing can spend money while this is true.** |
| `DATABASE_URL` | local | Must use the `postgresql+asyncpg://` driver. |
| `ORCHESTRATOR` | `temporal` | `temporal` or `state_machine`. |
| `LLM_PROVIDER` | `fake` | `fake`, `anthropic`, `openai` or `groq`. `DRY_RUN` doesn't force this one, because a dry run against a real model is often the point. |
| `TWILIO_PROVIDER` / `ELEVENLABS_PROVIDER` | `fake` | Switch one vendor at a time to its real adapter. |
| `EMAIL_PROVIDER` / `EMAIL_FROM` | `fake` | `resend`, `sendgrid` or `smtp`. With `resend` or `sendgrid`, `EMAIL_FROM` must be on a **domain you have verified by DNS** — a free-mail or placeholder address **fails at startup**, because those providers refuse every send from one. `smtp` points at the local Mailpit and accepts anything. |
| `ELEVENLABS_WEBHOOK_SECRET` | empty | Without it, webhooks are recorded but flagged unverified, and refused in staging and production. |
| `ADMIN_API_KEY` | empty | Protects the operator console. Open locally when unset, refused in staging and production. |
| `PUBLIC_API_URL` | empty | Set it behind a tunnel or proxy. Twilio signs the public URL, so signature checks fail without it. |

Choosing a real provider without its credentials **fails at startup**, not
partway through a customer's provisioning.

### Migrations

```bash
cd backend
uv run alembic upgrade head                          # apply
uv run alembic revision --autogenerate -m "add x"    # generate, then REVIEW it
uv run alembic current                               # where am I
```

The recent feature migrations are all additive and nullable, so existing rows
keep working: `business_profiles.greeting_custom` (custom greetings),
`tenants.requested_number` (choosing a number) and `users.password_hash`
(password sign-in).

The two newest are **`9b4e1c73af52`** (`website_onboardings`, plus four nullable
columns on `business_profiles`) and **`c41d7e9a2b60`** (`contacts`). Both add
tables and nullable columns only — no existing column is altered or dropped, so
rolling forward does not require downtime and rolling back loses only the new
data. Both were verified locally against an existing database *and* against a
freshly built one, upgrade and downgrade.

---

## Testing

```bash
# backend: lint, types, tests
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run pytest                 # 1,074 passed

# frontend: lint, types, tests, production build
cd frontend
npm run lint && npm run typecheck && npm run test && npm run build   # 188 tests
```

> Run the backend suite **on its own**. Two pytest sessions sharing the test
> database deadlock on `TRUNCATE` and report failures that aren't real.

Backend tests run against **real PostgreSQL**, never SQLite, because the schema
relies on JSONB, partial unique indexes and `FOR UPDATE SKIP LOCKED`. The test
database is built by running **the migrations**, so a model change without a
migration fails in tests rather than in production. Every vendor is a stateful
fake in the suite: the fakes enforce uniqueness, reject numbers that are already
sold, and can be told to fail, so retry, adoption and compensation paths are
genuinely exercised.

---

## Known limitations

### Website-based setup

- **English only.** The call path sends `"language": "en"` and nothing else, so
  the wizard offers one language and the API refuses any other value with a 422
  rather than accepting a setting it would ignore.
- **SMS templates are saved, not sent.** They are stored with the receptionist
  and are off by default. Sending them needs a registered A2P 10DLC campaign,
  which does not exist yet; the UI says so where they are shown.
- **A thin or JavaScript-rendered site yields little.** The crawler reads
  server-rendered HTML. A site that paints its content with JavaScript will
  produce few facts, and the owner has to fill them in by hand.
- **The crawl has not been run against a large sample of real sites.** The
  budgets and the extraction are covered by tests against fixed HTML; how often
  a real small-business site produces something useful is not yet measured.

### Contacts

- **A contact is created after a call is summarised,** not when the call
  arrives. A call whose summarisation keeps failing stays visible in Calls but
  never appears in Contacts.
- **Phone numbers are US-only** (NANP). Manual entry and import reject anything
  else. Capture stores the number exactly as the provider sent it, so in theory
  a non-E.164 inbound number could create a contact that manual entry could not
  have matched.
- **Export is CSV, not XLSX**, and has no row cap: a very large contact list is
  built in memory before it is sent.
- **Blocking does not refuse calls.** It marks and filters the contact. A
  business may reasonably expect otherwise, which is why the dialog says what it
  does and does not do.
- The contacts page **no longer shows** the last summary, last intent or an
  urgency flag per caller, which the older derived view did. That detail now
  lives on the call itself.

### Platform

- **Email needs a verified sending domain.** Resend and SendGrid both refuse
  every send whose `from` domain they have not verified by DNS, so until a
  domain is verified nothing reaches a customer. The app now refuses to start
  in that state rather than failing one send at a time: a free-mail or
  placeholder `EMAIL_FROM` with a real provider selected is a startup error.
  Password sign-in works without email.
- **Email verification is recorded but not enforced.** Registering sends a
  welcome email carrying a confirmation link, and `POST /auth/verify-email`
  records the result — but nothing is gated on it yet. Making it a requirement
  is a deliberate product decision that has not been taken.
- **Password reset has no UI yet.** The API is complete
  (`POST /auth/password-reset`, `POST /auth/password-reset/complete`), and the
  emailed link points at `/auth/reset`, which is not built. The magic link is
  still the way back in from the browser until it is.
- **A full live run isn't verified end to end.** Real number, real agent, real
  inbound call. The real adapters are tested against recorded request and
  response shapes, but vendor behaviour only shows up on a live account.
- **Limited self-service editing.** Owners can now delete a receptionist and
  manage their contacts, but editing a live configuration, pausing the
  receptionist, managing numbers or team members, and closing an account aren't
  built yet.
- **Tenants created before password sign-in have no account.** A new signup
  creates one. Tenants that are still live and predate the change need a
  backfill.
- **Timezones are approximate for split states.** The area-code table records
  each state's main timezone.

## Roadmap

1. Verify a sending domain, then build the `/auth/reset` and `/auth/verify` pages the
   reset and confirmation emails link to.
2. A complete live run against real Twilio and ElevenLabs, recorded as contract tests.
3. Customer self-service: edit configuration, pause the receptionist, manage numbers and team.
4. Decide what blocking a contact should actually do, and build it deliberately.
5. Measure the website crawl against a real sample of small-business sites.
6. SMS follow-ups, which need a registered A2P 10DLC campaign — the templates are already drafted and stored.
7. Calendar and website integrations (booking, embedded widget).
8. Observability: OpenTelemetry traces by tenant and call, SLOs and alerting.

---

## Documents

| File | Purpose |
|---|---|
| [`docs/00_DECISIONS.md`](docs/00_DECISIONS.md) | Architecture decisions and why |
| [`docs/PRODUCTION_MIGRATION_PLAN.md`](docs/PRODUCTION_MIGRATION_PLAN.md) | What each production module did and why |
| [`docs/LOCAL_PRODUCTION_SETUP.md`](docs/LOCAL_PRODUCTION_SETUP.md) | Running the full local stack |
| [`docs/TELEPHONY.md`](docs/TELEPHONY.md) | Both call paths, and what a caller hears when things break |
| [`docs/WEBHOOKS.md`](docs/WEBHOOKS.md) | Webhook setup, signatures, replay protection |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Incident response and recovery |
| [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md) | The full journey, and what it does not prove |
| [`docs/LIVE_E2E_TEST_READINESS.md`](docs/LIVE_E2E_TEST_READINESS.md) | Checklist before a live end-to-end run |
| [`docs/gtm/`](docs/gtm/) | Go-to-market plan and a cost breakdown per deployment, tool and call |
| [`backend/README.md`](backend/README.md) | Backend conventions and workflow |
