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
