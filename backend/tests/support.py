"""Test helpers with no fixture dependencies.

Lives apart from ``conftest`` so that fixture modules can import it without
creating an import cycle back through conftest.
"""

from __future__ import annotations

from app.core.config import Settings

#: Never connected to by the non-database tests; it only has to be a valid
#: async URL so that Settings construction succeeds.
STUB_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:55432/test"


def build_settings(**overrides: object) -> Settings:
    """Settings with test-safe defaults, overridable per test."""
    values: dict[str, object] = {
        "environment": "test",
        "debug": False,
        "dry_run": True,
        "log_level": "WARNING",
        "log_format": "json",
        "database_url": STUB_DATABASE_URL,
        # Do not let a developer's local .env leak into the suite.
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]
