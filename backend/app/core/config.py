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
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent

#: Absolute, so `uvicorn` from backend/ and `pytest` from the repo root both
#: find the same file. A backend-local .env wins over the shared one.
ENV_FILES: tuple[Path, ...] = (_REPO_ROOT / ".env", _BACKEND_ROOT / ".env")

Environment = Literal["local", "test", "development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]

LLMProviderName = Literal["fake", "anthropic", "openai"]
TwilioProviderName = Literal["fake", "twilio"]
ElevenLabsProviderName = Literal["fake", "elevenlabs"]
EmailProviderName = Literal["fake", "resend", "sendgrid"]


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
    #: Config generation is once per tenant and quality-critical → strong model.
    llm_config_model: str = "claude-opus-5"
    #: Summarization is per call and easy → small, fast model.
    llm_summary_model: str = "claude-haiku-4-5-20251001"
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
        if missing:
            raise ValueError(
                f"missing credentials for the selected providers: {', '.join(missing)}"
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, built once.

    Used by real entrypoints (ASGI app, worker, CLI). Tests construct
    :class:`Settings` directly and pass it to ``create_app``.
    """
    return Settings()
