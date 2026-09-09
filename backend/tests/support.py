"""Test helpers with no fixture dependencies.

Lives apart from ``conftest`` so that fixture modules can import it without
creating an import cycle back through conftest.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI

from app.core.config import Settings
from app.services.status_tokens import issue_status_token

#: Never connected to by the non-database tests; it only has to be a valid
#: async URL so that Settings construction succeeds.
STUB_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:55432/test"


#: Placeholders that satisfy `Settings._production_hardening`.
#:
#: A test that asks for `environment="production"` is almost always exercising
#: some *other* production behaviour — that docs are hidden, that a property
#: flips — and should not have to restate the whole secret inventory to get
#: there. These are applied only for production-like environments, and only
#: where the test did not set them itself, so a test that specifically targets
#: the hardening validator still sees exactly what it passed.
_PRODUCTION_PLACEHOLDERS: dict[str, object] = {
    "auth_secret_key": "x" * 32,
    "admin_api_key": "test-admin-key",
    "elevenlabs_webhook_secret": "test-webhook-secret",
    "session_cookie_secure": True,
    "log_format": "json",
    "debug": False,
    "billing_gate_enabled": True,
}


def build_settings(**overrides: object) -> Settings:
    """Settings with test-safe defaults, overridable per test."""
    values: dict[str, object] = {
        "environment": "test",
        "debug": False,
        "dry_run": True,
        "log_level": "WARNING",
        "log_format": "json",
        "database_url": STUB_DATABASE_URL,
        # The default is now `temporal`, but most of this suite drives the
        # polling engine directly and asserts on its scheduling. Pinned here
        # so those tests keep testing what they were written to test; the
        # Temporal path has its own file (test_temporal_workflow.py).
        "orchestrator": "state_machine",
        # Fixed, so signature assertions are reproducible across runs. Not a
        # real credential, and never used outside the suite.
        "auth_secret_key": "test-auth-secret-key-not-used-anywhere-real",
        # Do not let a developer's local .env leak into the suite.
        "_env_file": None,
    }
    values.update(overrides)
    if values.get("environment") in ("staging", "production"):
        for key, placeholder in _PRODUCTION_PLACEHOLDERS.items():
            values.setdefault(key, placeholder)
    return Settings(**values)  # type: ignore[arg-type]


def grant(app: FastAPI, tenant_id: uuid.UUID | str) -> dict[str, str]:
    """Query params carrying a signed status grant for ``tenant_id``.

    Tenant reads require a credential now — a bare UUID in the path grants
    nothing (see app.api.auth_deps.require_tenant_read). The status grant is the
    right one for these tests: they exercise the post-signup status page, which
    is precisely the caller that has no account yet.
    """
    return {"status_token": issue_status_token(app.state.settings, uuid.UUID(str(tenant_id)))}
