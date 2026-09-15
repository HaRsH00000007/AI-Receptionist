"""Typed application configuration.

Settings are read from the environment (and a local ``.env``) exactly once per
process and then injected — never imported as a module-level global. Request
handlers reach them through :func:`app.api.deps.get_settings_dep`, which reads
the instance the application factory stored on ``app.state``. That is what
lets a test build an app with different settings without touching the
environment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from app.models.enums import TenantPlan

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent

#: Absolute, so `uvicorn` from backend/ and `pytest` from the repo root both
#: find the same file. A backend-local .env wins over the shared one.
ENV_FILES: tuple[Path, ...] = (_REPO_ROOT / ".env", _BACKEND_ROOT / ".env")

Environment = Literal["local", "test", "development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]

LLMProviderName = Literal["fake", "anthropic", "openai", "groq"]

#: The stock model ids. They are Anthropic's because `anthropic` is the
#: reference real provider; every other vendor publishes its own catalogue and
#: therefore needs these set explicitly. Named here so a validator can tell
#: "the operator chose this" from "nobody chose anything".
DEFAULT_LLM_CONFIG_MODEL = "claude-opus-5"
DEFAULT_LLM_SUMMARY_MODEL = "claude-haiku-4-5-20251001"
TwilioProviderName = Literal["fake", "twilio"]
ElevenLabsProviderName = Literal["fake", "elevenlabs"]
EmailProviderName = Literal["fake", "resend", "sendgrid", "smtp"]
PaymentProviderName = Literal["fake", "stripe"]
ObjectStorageProviderName = Literal["fake", "s3"]

#: How provisioning is orchestrated.
#:
#: ``state_machine`` is the POC's polling worker; ``temporal`` is the production
#: path. Both are kept deliberately: the flag is what lets Temporal be proven
#: against the same test suite before the state machine is retired, rather than
#: swapping orchestration and hoping (see docs/PRODUCTION_MIGRATION_PLAN.md M4).
OrchestratorName = Literal["state_machine", "temporal"]

#: Which ElevenLabs agent a tenant is served by.
#:
#: ``per_tenant`` is the POC shape — one vendor agent per customer.
#: ``shared_vertical`` is the production shape — ~5 agents, one per business
#: type, with per-call dynamic variables. Per-tenant so that tenants migrate in
#: batches rather than all at once.
AgentModeName = Literal["per_tenant", "shared_vertical"]

#: How a call physically reaches the voice agent.
#:
#: ``elevenlabs_native`` is the POC path: the number is imported into
#: ElevenLabs and the vendor owns routing end to end. Simple, and it works, but
#: we see nothing — no call record until the post-call webhook, no fallback when
#: the vendor is down, no way to route one number two ways.
#:
#: ``twiml_stream`` is the production path: Twilio posts to us, we decide, and
#: we hand the media stream to the vendor with ``<Connect><Stream>``. We own
#: routing, we can answer with a voicemail when the vendor is unreachable, and
#: every call is recorded at the moment it arrives rather than after it ends.
#:
#: Both are kept. The POC path stays the default so that existing tenants are
#: unaffected until they are migrated deliberately.
CallPathName = Literal["elevenlabs_native", "twiml_stream"]


class Settings(BaseSettings):
    """Everything the process needs to know, validated at startup."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application -----------------------------------------------------
    app_name: str = "AI Receptionist API"
    environment: Environment = "local"
    debug: bool = False

    # ---- Safety ----------------------------------------------------------
    # Guards every side effect that spends money or mutates a vendor account.
    # Defaults to True so that forgetting to set it cannot cost anything.
    # See docs/00_DECISIONS.md section 7.1.
    dry_run: bool = True

    # ---- HTTP ------------------------------------------------------------
    api_v1_prefix: str = "/api/v1"
    correlation_id_header: str = "X-Correlation-ID"
    # Comma-separated in the environment; use `cors_origin_list` in code.
    cors_origins: str = "http://localhost:3000"

    # ---- Logging ---------------------------------------------------------
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"

    # ---- Database --------------------------------------------------------
    # Required, so that a misconfigured deployment fails at startup rather than
    # on the first query. Must use an async driver.
    database_url: str = Field(min_length=1)
    db_echo: bool = False
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    # Server-side cap, so a pathological query cannot wedge a worker forever.
    db_statement_timeout_ms: int = Field(default=30_000, ge=1_000)

    # ---- Signup ----------------------------------------------------------
    #: Per-IP signups allowed inside `signup_rate_limit_window_s`. A signup can
    #: spend real money, so the form is never left ungated.
    signup_rate_limit: int = Field(default=10, ge=1)
    signup_rate_limit_window_s: int = Field(default=60, ge=1)
    #: Optional shared secret for the Tally webhook adapter.
    tally_signing_secret: SecretStr = SecretStr("")

    # ---- Worker ----------------------------------------------------------
    worker_poll_interval_s: float = Field(default=2.0, gt=0)
    #: How many runs one poll may claim. Small, because each claim holds a row
    #: lock for the duration of a step.
    worker_batch_size: int = Field(default=5, ge=1)
    #: Attempts per step before the run is failed and compensation begins.
    provisioning_max_attempts: int = Field(default=5, ge=1)
    #: Backoff ladder in seconds; the last value repeats if attempts exceed it.
    provisioning_backoff_s: str = "5,30,120,600"
    #: A step that has been RUNNING longer than this was orphaned by a crash and
    #: is eligible to be retried.
    provisioning_step_timeout_s: int = Field(default=300, ge=10)
    call_summary_max_attempts: int = Field(default=3, ge=1)

    # ---- Providers -------------------------------------------------------
    llm_provider: LLMProviderName = "fake"
    twilio_provider: TwilioProviderName = "fake"
    elevenlabs_provider: ElevenLabsProviderName = "fake"
    email_provider: EmailProviderName = "fake"
    #: Seconds. Applies to every outbound provider HTTP call.
    provider_timeout_s: float = Field(default=30.0, gt=0)

    # ---- LLM -------------------------------------------------------------
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    #: Groq speaks OpenAI's chat-completions API, so it needs no client of its
    #: own — only its own base URL and key. The base URL already carries the
    #: ``/openai/v1`` prefix, which is why the adapter posts to
    #: ``/chat/completions`` rather than ``/v1/chat/completions``.
    groq_api_key: SecretStr = SecretStr("")
    groq_api_base_url: str = "https://api.groq.com/openai/v1"
    #: Config generation is once per tenant and quality-critical → strong model.
    llm_config_model: str = DEFAULT_LLM_CONFIG_MODEL
    #: Summarization is per call and easy → small, fast model.
    llm_summary_model: str = DEFAULT_LLM_SUMMARY_MODEL
    llm_max_tokens: int = Field(default=4096, ge=256)
    #: Deterministic by default: the same form should produce the same config.
    llm_temperature: float = Field(default=0.0, ge=0.0, le=1.0)

    # ---- Twilio ----------------------------------------------------------
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_api_base_url: str = "https://api.twilio.com"

    # ---- ElevenLabs ------------------------------------------------------
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_api_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_webhook_secret: SecretStr = SecretStr("")
    #: Voice per greeting style, as "style:voice_id" pairs. Deterministic —
    #: an LLM never picks a voice.
    elevenlabs_voice_map: str = (
        "professional:21m00Tcm4TlvDq8ikWAM,"
        "friendly:EXAVITQu4vr4xnSDxMaL,"
        "formal:onwK4e9ZLuTAKqWW03F9"
    )
    elevenlabs_default_voice_id: str = "21m00Tcm4TlvDq8ikWAM"

    # ---- Email -----------------------------------------------------------
    resend_api_key: SecretStr = SecretStr("")
    sendgrid_api_key: SecretStr = SecretStr("")
    email_from: str = "AI Receptionist <onboarding@example.com>"
    resend_api_base_url: str = "https://api.resend.com"
    sendgrid_api_base_url: str = "https://api.sendgrid.com"

    # ---- Admin -----------------------------------------------------------
    #: Shared secret for /admin routes. Not an auth system — just enough that
    #: the retry and abandon actions are not open to the internet.
    admin_api_key: SecretStr = SecretStr("")

    # ---- Orchestration ---------------------------------------------------
    #: Which engine drives provisioning.
    #:
    #: Temporal since M4. The polling state machine is retained as a documented
    #: fallback — it is the path the older tests prove, so a Temporal outage has
    #: an answer that is not "provisioning is down" — but exactly one of the two
    #: is authoritative at a time. `app.worker` refuses to claim provisioning
    #: runs unless it is the chosen one, because two orchestrators over the same
    #: runs could both buy a phone number.
    orchestrator: OrchestratorName = "temporal"
    #: Default agent topology for newly provisioned tenants. Existing tenants
    #: keep whatever their own `tenants.agent_mode` column says.
    default_agent_mode: AgentModeName = "per_tenant"
    #: Shared vertical agents, as "salon:agent_a,legal:agent_b,generic:agent_z".
    #:
    #: Configured rather than discovered: agent ids are account-specific and
    #: differ between test and live ElevenLabs accounts, exactly like Stripe
    #: price ids. Nothing in the application creates a shared agent — code that
    #: can create one can create a second by accident, and two agents serving
    #: one vertical is a split-brain where half the customers get a stale prompt.
    shared_agent_ids: str = ""

    # ---- Redis -----------------------------------------------------------
    # Redis is a performance and coordination layer, never the source of truth.
    # Postgres answers the same questions, more slowly; every cache read has a
    # database fallback. Losing Redis must degrade latency, not correctness.
    redis_url: str = "redis://localhost:6379/0"
    #: When false, every cache/lock/rate-limit helper takes its Postgres or
    #: in-process fallback path. This is how the "Redis is down" test variant
    #: runs, and how a deployment survives an ElastiCache failover.
    redis_enabled: bool = False
    redis_socket_timeout_s: float = Field(default=0.25, gt=0)
    #: Deliberately short. Redis sits on the audible call path, where waiting
    #: on a slow cache is worse than missing it and reading Postgres.
    redis_connect_timeout_s: float = Field(default=0.25, gt=0)
    #: TTLs, seconds. Keyed per use so a hot-config change and a dedupe window
    #: are not accidentally coupled.
    redis_number_lookup_ttl_s: int = Field(default=300, ge=1)
    redis_config_cache_ttl_s: int = Field(default=600, ge=1)
    redis_webhook_dedupe_ttl_s: int = Field(default=86_400, ge=1)
    redis_provision_lock_ttl_s: int = Field(default=300, ge=1)

    # ---- Temporal --------------------------------------------------------
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "ai-receptionist"
    #: Whole-workflow ceiling. Provisioning that has not finished in an hour is
    #: not going to; it needs an operator, not another retry.
    temporal_workflow_timeout_s: int = Field(default=3_600, ge=60)
    #: Per-activity ceiling, sized for the slowest vendor call plus headroom.
    temporal_activity_timeout_s: int = Field(default=120, ge=5)
    temporal_tls_enabled: bool = False
    #: Temporal Cloud uses mTLS. Local dev uses neither.
    temporal_client_cert_path: str = ""
    temporal_client_key_path: str = ""

    # ---- Object storage --------------------------------------------------
    # MinIO locally, S3 in AWS — the same S3 API behind one interface, so the
    # move is an endpoint change rather than a code change.
    object_storage_provider: ObjectStorageProviderName = "fake"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "ai-receptionist"
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    #: MinIO needs path-style addressing; S3 prefers virtual-host style.
    s3_use_path_style: bool = True

    # ---- Billing ---------------------------------------------------------
    payment_provider: PaymentProviderName = "fake"
    stripe_secret_key: SecretStr = SecretStr("")
    stripe_publishable_key: str = ""
    stripe_webhook_secret: SecretStr = SecretStr("")
    stripe_api_base_url: str = "https://api.stripe.com"
    #: The money gate. When true, provisioning refuses to purchase a number
    #: unless the tenant holds an active subscription or a granted trial. This
    #: is the single largest commercial leak in the POC (docs/02_PLAN_
    #: PRODUCTION.md s4), so it defaults to ON and must be disabled explicitly.
    billing_gate_enabled: bool = True
    #: Stripe price id -> plan, as "price_x:pro,price_y:enterprise".
    #: Configured rather than inferred: price ids are account-specific and
    #: differ between test and live mode. Use `stripe_price_plan_map`.
    stripe_price_plans: str = ""
    #: Days of trial granted at signup when no card is on file. Zero means a
    #: card is required before any number is bought.
    #: How often a run parked in BILLING_BLOCKED re-checks entitlement on its
    #: own. The Stripe webhook un-parks it immediately; this is the fallback
    #: for a webhook that is never delivered, so a paying customer is never
    #: stranded by a missed delivery.
    billing_recheck_interval_s: int = Field(default=300, ge=30)
    trial_days: int = Field(default=14, ge=0)
    trial_included_minutes: int = Field(default=60, ge=0)

    # ---- Authentication --------------------------------------------------
    #: Signing key for session cookies and magic-link tokens. Required in a
    #: production-like environment; see `_production_hardening` below.
    auth_secret_key: SecretStr = SecretStr("")
    session_cookie_name: str = "ai_receptionist_session"
    session_ttl_s: int = Field(default=1_209_600, ge=300)  # 14 days
    #: Short by design: a magic link is a bearer credential sitting in an inbox.
    magic_link_ttl_s: int = Field(default=900, ge=60)
    #: Cookies are Secure everywhere except plain-HTTP local development.
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    login_rate_limit: int = Field(default=5, ge=1)
    login_rate_limit_window_s: int = Field(default=300, ge=1)

    # ---- Observability ---------------------------------------------------
    otel_enabled: bool = False
    otel_service_name: str = "ai-receptionist-api"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    #: 1.0 locally so nothing is missed while developing; lowered in production
    #: where the volume is real and the cost is per-span.
    otel_traces_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    # ---- SMTP (local Mailpit) --------------------------------------------
    # Mailpit accepts anything on 1025 and shows it in a web UI on 8025. It is
    # how local development gets a real send/deliver/render loop without any
    # risk of mailing an actual customer.
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=1025, ge=1, le=65_535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_use_tls: bool = False

    #: How often the worker runs retention and usage rollups. Slow on purpose:
    #: both are idempotent catch-up jobs, not latency-sensitive work.
    maintenance_interval_s: int = Field(default=3_600, ge=60)

    # ---- Data retention --------------------------------------------------
    #: Days a transcript is kept before the retention job removes it. Per-plan
    #: overrides live in the database; this is the floor.
    transcript_retention_days: int = Field(default=90, ge=1)
    recording_retention_days: int = Field(default=30, ge=1)

    # ---- Telephony -------------------------------------------------------
    #: Which call path new and migrated tenants use. See `CallPathName`.
    call_path: CallPathName = "elevenlabs_native"
    #: The ElevenLabs conversational websocket that `<Connect><Stream>` targets.
    #: Configurable because it is a vendor endpoint, not a constant of ours.
    elevenlabs_stream_url: str = "wss://api.elevenlabs.io/v1/convai/conversation"
    #: Voice used for the fallback announcements Twilio speaks directly. Only
    #: reached when ElevenLabs is not carrying the call.
    twilio_fallback_voice: str = "Polly.Joanna"
    #: Seconds of voicemail accepted when the agent cannot take the call.
    voicemail_max_length_s: int = Field(default=120, ge=10, le=600)
    #: Announce that the call is recorded. Recording consent is jurisdictional;
    #: where it is required this must be on, and the announcement is injected
    #: into the greeting rather than left to the prompt to remember.
    recording_consent_announcement: bool = False

    # ---- Vendor circuit breakers -----------------------------------------
    #: Consecutive failures before a vendor is treated as down. Low enough to
    #: matter during a real outage, high enough that one blip does not trip it.
    circuit_failure_threshold: int = Field(default=5, ge=1)
    #: How long a circuit stays open before one probe is allowed through.
    circuit_cooldown_s: float = Field(default=30.0, gt=0)

    # ---- Webhook security ------------------------------------------------
    #: Refuse any delivery whose signature does not verify.
    #:
    #: Always true in a production-like environment — see
    #: `webhooks_require_signature`. This setting only *raises* the bar in
    #: local and test environments, where an unsigned delivery is otherwise
    #: recorded with `signature_valid=False` so a DRY_RUN loop stays usable.
    #: It can never lower it: there is no way to switch verification off in
    #: staging or production.
    require_webhook_signature: bool = False
    #: How old a signed delivery may be before it is refused as a replay.
    #: Applies to providers that sign a timestamp (ElevenLabs, Stripe).
    webhook_tolerance_s: int = Field(default=1_800, ge=60)

    # ---- Public URLs -----------------------------------------------------
    #: Used in notification emails so a recipient can reach the status page.
    public_app_url: str = "http://localhost:3000"
    #: The externally visible base URL of this API. Twilio signs the *public*
    #: URL it called, so behind a tunnel or proxy the URL this process sees
    #: (http://localhost:8000/...) is not the one that was signed and every
    #: signature check would fail. Leave blank when there is no proxy.
    public_api_url: str = ""

    # ---------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------
    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        """Reject a sync driver URL rather than failing on the first query.

        ``postgresql://`` silently selects psycopg2, which cannot run under the
        async engine; the resulting error appears far from its cause.
        """
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the postgresql+asyncpg:// driver")
        return value

    @field_validator("api_v1_prefix")
    @classmethod
    def _prefix_shape(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("api_v1_prefix must start with '/'")
        return value.rstrip("/")

    @field_validator("provisioning_backoff_s")
    @classmethod
    def _backoff_shape(cls, value: str) -> str:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("provisioning_backoff_s needs at least one delay")
        for part in parts:
            if not part.isdigit():
                raise ValueError("provisioning_backoff_s must be comma-separated integers")
        return value

    @staticmethod
    def _blank(secret: SecretStr) -> bool:
        """Whether a secret is unset.

        A ``SecretStr`` is always truthy, so ``if not settings.api_key`` silently
        does the wrong thing. Every emptiness check goes through here.
        """
        return not secret.get_secret_value()

    @model_validator(mode="after")
    def _credentials_present_for_real_providers(self) -> Self:
        """A real provider without its credential fails at startup, not mid-run.

        Discovering a missing Twilio token halfway through provisioning means a
        tenant stuck between states; discovering it at boot means a fixed .env.
        """
        missing: list[str] = []
        if self.effective_llm_provider == "anthropic" and self._blank(self.anthropic_api_key):
            missing.append("ANTHROPIC_API_KEY")
        if self.effective_llm_provider == "openai" and self._blank(self.openai_api_key):
            missing.append("OPENAI_API_KEY")
        if self.effective_llm_provider == "groq" and self._blank(self.groq_api_key):
            missing.append("GROQ_API_KEY")
        if self.effective_twilio_provider == "twilio" and (
            not self.twilio_account_sid or self._blank(self.twilio_auth_token)
        ):
            missing.append("TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN")
        if self.effective_elevenlabs_provider == "elevenlabs" and self._blank(
            self.elevenlabs_api_key
        ):
            missing.append("ELEVENLABS_API_KEY")
        if self.effective_email_provider == "resend" and self._blank(self.resend_api_key):
            missing.append("RESEND_API_KEY")
        if self.effective_email_provider == "sendgrid" and self._blank(self.sendgrid_api_key):
            missing.append("SENDGRID_API_KEY")
        if self.effective_payment_provider == "stripe" and self._blank(self.stripe_secret_key):
            missing.append("STRIPE_SECRET_KEY")
        if self.effective_object_storage_provider == "s3" and (
            not self.s3_access_key_id or self._blank(self.s3_secret_access_key)
        ):
            missing.append("S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY")
        if self.effective_email_provider == "smtp" and not self.smtp_host:
            missing.append("SMTP_HOST")
        if missing:
            raise ValueError(
                f"missing credentials for the selected providers: {', '.join(missing)}"
            )
        return self

    @model_validator(mode="after")
    def _llm_models_match_the_selected_vendor(self) -> Self:
        """A vendor-specific model id must be chosen explicitly.

        The stock ids are Anthropic's. Pointing a different vendor at them
        produces a 404 from that vendor — and it produces it *during*
        provisioning, where a tenant is left mid-flight, rather than at boot
        where it is a one-line fix. Same reasoning as the credential check
        above: fail early and name the variable to set.

        Only vendors with a genuinely different catalogue are checked. `openai`
        is deliberately excluded: it has always required the operator to pick
        an id, and adding a new failure mode to a provider this change is not
        about would be a regression, not a fix.
        """
        if self.effective_llm_provider != "groq":
            return self
        unset = [
            name
            for name, value, default in (
                ("LLM_CONFIG_MODEL", self.llm_config_model, DEFAULT_LLM_CONFIG_MODEL),
                ("LLM_SUMMARY_MODEL", self.llm_summary_model, DEFAULT_LLM_SUMMARY_MODEL),
            )
            if value == default
        ]
        if unset:
            raise ValueError(
                "LLM_PROVIDER=groq needs Groq model ids: "
                f"{', '.join(unset)} still hold the default Anthropic model. "
                "Set them to models from https://console.groq.com/docs/models"
            )
        return self

    @model_validator(mode="after")
    def _temporal_tls_material_present(self) -> Self:
        """mTLS needs both halves of the pair, or neither.

        Temporal Cloud rejects a client presenting a certificate without its
        key with an error that names neither, so the mismatch is caught here.
        """
        cert, key = self.temporal_client_cert_path, self.temporal_client_key_path
        if bool(cert) != bool(key):
            raise ValueError(
                "TEMPORAL_CLIENT_CERT_PATH and TEMPORAL_CLIENT_KEY_PATH must be set together"
            )
        return self

    @model_validator(mode="after")
    def _production_hardening(self) -> Self:
        """Refuse to boot a staging/production process with local defaults.

        Every item here is something that is merely inconvenient locally and
        genuinely dangerous in production: an open admin surface, unsigned
        webhooks, a guessable session key, verbose debug output, or a billing
        gate someone turned off to test something and forgot to turn back on.
        Failing at startup makes the misconfiguration a deploy failure instead
        of an incident.
        """
        if not self.is_production_like:
            return self

        problems: list[str] = []
        if self.debug:
            problems.append("DEBUG must be false")
        if self._blank(self.auth_secret_key):
            problems.append("AUTH_SECRET_KEY is required")
        elif len(self.auth_secret_key.get_secret_value()) < 32:
            problems.append("AUTH_SECRET_KEY must be at least 32 characters")
        if self._blank(self.admin_api_key):
            problems.append("ADMIN_API_KEY is required")
        if self._blank(self.elevenlabs_webhook_secret):
            problems.append("ELEVENLABS_WEBHOOK_SECRET is required")
        if self.effective_payment_provider == "stripe" and self._blank(self.stripe_webhook_secret):
            problems.append("STRIPE_WEBHOOK_SECRET is required when Stripe is enabled")
        if not self.billing_gate_enabled:
            problems.append("BILLING_GATE_ENABLED must not be disabled")
        if not self.session_cookie_secure:
            problems.append("SESSION_COOKIE_SECURE must be true")
        if self.log_format != "json":
            problems.append("LOG_FORMAT must be json so logs are machine-readable")
        if problems:
            raise ValueError(
                f"unsafe configuration for environment={self.environment}: " + "; ".join(problems)
            )
        return self

    # ---------------------------------------------------------------------
    # Derived
    # ---------------------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        """`CORS_ORIGINS` split into individual origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production_like(self) -> bool:
        """True where mistakes are expensive and output must be machine-readable."""
        return self.environment in ("staging", "production")

    @property
    def webhooks_require_signature(self) -> bool:
        """Whether an unverified delivery is refused outright.

        Production-like environments always require one; the setting can only
        turn it on elsewhere, never off there. Expressed as `or` rather than a
        settable default so that no configuration mistake can disable signature
        checking in production.
        """
        return self.is_production_like or self.require_webhook_signature

    @property
    def backoff_schedule_s(self) -> tuple[int, ...]:
        return tuple(
            int(part.strip()) for part in self.provisioning_backoff_s.split(",") if part.strip()
        )

    @property
    def voice_map(self) -> dict[str, str]:
        """Greeting style → ElevenLabs voice id. Deterministic by construction."""
        pairs: dict[str, str] = {}
        for entry in self.elevenlabs_voice_map.split(","):
            style, _, voice = entry.partition(":")
            if style.strip() and voice.strip():
                pairs[style.strip().lower()] = voice.strip()
        return pairs

    # ---- Effective provider selection ------------------------------------
    # DRY_RUN forces a fake for anything that spends money or mutates a vendor
    # account. The LLM is exempt: reading a real model costs cents and is often
    # exactly what you want to exercise during a dry run.
    @property
    def effective_llm_provider(self) -> LLMProviderName:
        return self.llm_provider

    @property
    def effective_twilio_provider(self) -> TwilioProviderName:
        return "fake" if self.dry_run else self.twilio_provider

    @property
    def effective_elevenlabs_provider(self) -> ElevenLabsProviderName:
        return "fake" if self.dry_run else self.elevenlabs_provider

    @property
    def effective_email_provider(self) -> EmailProviderName:
        return "fake" if self.dry_run else self.email_provider

    @property
    def effective_payment_provider(self) -> PaymentProviderName:
        """Stripe is a money mover, so DRY_RUN forces the fake.

        Note that this is belt-and-braces: even the real adapter should only
        ever hold a `sk_test_` key outside production. The gate exists so that
        a mis-set key cannot be reached by a dry run at all.
        """
        return "fake" if self.dry_run else self.payment_provider

    @property
    def effective_object_storage_provider(self) -> ObjectStorageProviderName:
        """Object storage does not spend money, but it does leave artifacts.

        A dry run should not litter a real bucket with recordings, so it uses
        the in-memory implementation like every other side effect.
        """
        return "fake" if self.dry_run else self.object_storage_provider

    # ---- Derived infrastructure ------------------------------------------
    @property
    def stripe_price_plan_map(self) -> dict[str, TenantPlan]:
        """`STRIPE_PRICE_PLANS` parsed into price id -> plan.

        An unparseable or unknown plan name is skipped rather than raising: a
        typo here must not stop the process booting, and an unmapped price
        leaves a subscription's plan unchanged instead of downgrading it.
        """
        # Imported here, not at module scope: app.models imports Settings, so
        # a top-level import would be a cycle.
        from app.models.enums import TenantPlan

        valid = {member.value: member for member in TenantPlan}
        pairs: dict[str, TenantPlan] = {}
        for entry in self.stripe_price_plans.split(","):
            price, _, plan = entry.partition(":")
            price, plan = price.strip(), plan.strip().lower()
            if price and plan in valid:
                pairs[price] = valid[plan]
        return pairs

    @property
    def temporal_tls_material(self) -> tuple[str, str] | None:
        """The mTLS pair, or None when connecting without client certificates."""
        if self.temporal_client_cert_path and self.temporal_client_key_path:
            return (self.temporal_client_cert_path, self.temporal_client_key_path)
        return None

    @property
    def uses_temporal(self) -> bool:
        return self.orchestrator == "temporal"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, built once.

    Used by real entrypoints (ASGI app, worker, CLI). Tests construct
    :class:`Settings` directly and pass it to ``create_app``.
    """
    return Settings()
