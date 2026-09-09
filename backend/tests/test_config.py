"""Configuration is validated at construction, not at first use."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from tests.support import build_settings


def test_dry_run_defaults_to_true() -> None:
    """Forgetting to set DRY_RUN must never be what spends money."""
    settings = build_settings()
    assert settings.dry_run is True


def test_database_url_is_required() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_settings(environment="prod")


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_settings(log_level="TRACE")


def test_api_prefix_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        build_settings(api_v1_prefix="api/v1")


def test_api_prefix_trailing_slash_is_normalised() -> None:
    assert build_settings(api_v1_prefix="/api/v1/").api_v1_prefix == "/api/v1"


def test_cors_origins_split_on_commas() -> None:
    settings = build_settings(cors_origins="http://a.test, http://b.test ,")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("local", False),
        ("test", False),
        ("development", False),
        ("staging", True),
        ("production", True),
    ],
)
def test_is_production_like(environment: str, expected: bool) -> None:
    assert build_settings(environment=environment).is_production_like is expected


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+asyncpg://u:p@h/db"
    assert settings.dry_run is False
    assert settings.log_level == "DEBUG"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Production hardening
#
# Every assertion here is a misconfiguration that is harmless on a laptop and
# an incident in production. They belong at startup, where the cost is a failed
# deploy, rather than at the first request that needs them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "unsafe_value", "expected_fragment"),
    [
        ("debug", True, "DEBUG"),
        ("auth_secret_key", "", "AUTH_SECRET_KEY is required"),
        ("auth_secret_key", "too-short", "at least 32 characters"),
        ("admin_api_key", "", "ADMIN_API_KEY"),
        ("elevenlabs_webhook_secret", "", "ELEVENLABS_WEBHOOK_SECRET"),
        ("billing_gate_enabled", False, "BILLING_GATE_ENABLED"),
        ("session_cookie_secure", False, "SESSION_COOKIE_SECURE"),
        ("log_format", "console", "LOG_FORMAT"),
    ],
)
def test_production_rejects_unsafe_configuration(
    field: str, unsafe_value: object, expected_fragment: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        build_settings(environment="production", **{field: unsafe_value})
    assert expected_fragment in str(excinfo.value)


def test_local_tolerates_what_production_refuses() -> None:
    """The same values must stay comfortable locally, or nobody develops."""
    settings = build_settings(environment="local", debug=True, log_format="console")
    assert settings.debug is True


def test_stripe_without_webhook_secret_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError) as excinfo:
        build_settings(
            environment="production",
            dry_run=False,
            payment_provider="stripe",
            stripe_secret_key="sk_test_x",
            stripe_webhook_secret="",
        )
    assert "STRIPE_WEBHOOK_SECRET" in str(excinfo.value)


# ---------------------------------------------------------------------------
# DRY_RUN forces fakes for anything that spends money or leaves artifacts
# ---------------------------------------------------------------------------


def test_dry_run_forces_fake_payment_provider() -> None:
    settings = build_settings(dry_run=True, payment_provider="stripe")
    assert settings.effective_payment_provider == "fake"


def test_dry_run_forces_fake_object_storage() -> None:
    settings = build_settings(dry_run=True, object_storage_provider="s3")
    assert settings.effective_object_storage_provider == "fake"


def test_real_payment_provider_requires_its_key() -> None:
    with pytest.raises(ValidationError) as excinfo:
        build_settings(dry_run=False, payment_provider="stripe", stripe_secret_key="")
    assert "STRIPE_SECRET_KEY" in str(excinfo.value)


def test_real_object_storage_requires_credentials() -> None:
    with pytest.raises(ValidationError) as excinfo:
        build_settings(dry_run=False, object_storage_provider="s3", s3_access_key_id="")
    assert "S3_ACCESS_KEY_ID" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


def test_temporal_tls_pair_must_be_complete() -> None:
    with pytest.raises(ValidationError) as excinfo:
        build_settings(temporal_client_cert_path="/tmp/client.pem")
    assert "must be set together" in str(excinfo.value)


def test_temporal_tls_material_is_none_without_certificates() -> None:
    assert build_settings().temporal_tls_material is None


def test_temporal_tls_material_pairs_the_paths() -> None:
    settings = build_settings(
        temporal_client_cert_path="/tmp/client.pem",
        temporal_client_key_path="/tmp/client.key",
    )
    assert settings.temporal_tls_material == ("/tmp/client.pem", "/tmp/client.key")


def test_orchestrator_defaults_to_the_state_machine() -> None:
    """Adding Temporal configuration must not, by itself, switch engines."""
    assert build_settings().orchestrator == "state_machine"
    assert build_settings().uses_temporal is False


# ---------------------------------------------------------------------------
# Redis is off until a module needs it, and off means "use the fallback"
# ---------------------------------------------------------------------------


def test_redis_is_disabled_by_default() -> None:
    assert build_settings().redis_enabled is False


def test_agent_mode_defaults_to_per_tenant() -> None:
    """The POC topology stays the default until tenants are migrated."""
    assert build_settings().default_agent_mode == "per_tenant"
