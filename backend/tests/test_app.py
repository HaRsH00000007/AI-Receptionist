"""The application factory and its error envelope."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import InvalidInputError, RetryableError
from app.main import create_app
from tests.support import build_settings


def test_factory_stores_settings_on_the_app() -> None:
    settings = build_settings()
    app = create_app(settings)
    assert app.state.settings is settings


def test_factory_builds_independent_apps() -> None:
    """Two apps in one process must not share state - the reason for the factory."""
    first = create_app(build_settings(app_name="first"))
    second = create_app(build_settings(app_name="second"))
    assert first.state.settings is not second.state.settings
    assert first.state.readiness is not second.state.readiness


def test_docs_are_exposed_outside_production() -> None:
    assert create_app(build_settings(environment="local")).docs_url == "/docs"


def test_docs_are_hidden_in_production() -> None:
    app = create_app(build_settings(environment="production"))
    assert app.docs_url is None
    assert app.openapi_url is None


async def _get(app: FastAPI, path: str) -> tuple[int, Any]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(path)
    return response.status_code, response.json()


async def test_app_errors_become_a_structured_envelope(app: FastAPI) -> None:
    @app.get("/_boom")
    async def boom() -> None:
        raise InvalidInputError("area code must be 3 digits", details={"field": "area_code"})

    status_code, body = await _get(app, "/_boom")

    assert status_code == 422
    assert body["error"] == {
        "code": "invalid_input",
        "message": "area code must be 3 digits",
        "retryable": False,
        "details": {"field": "area_code"},
    }
    assert body["correlation_id"] is not None


async def test_retryable_errors_say_so_to_the_client(app: FastAPI) -> None:
    @app.get("/_flaky")
    async def flaky() -> None:
        raise RetryableError("upstream timed out")

    status_code, body = await _get(app, "/_flaky")

    assert status_code == 503
    assert body["error"]["retryable"] is True


async def test_unexpected_errors_do_not_leak_internals(app: FastAPI) -> None:
    @app.get("/_unexpected")
    async def unexpected() -> None:
        raise RuntimeError("password=hunter2 in the connection string")

    status_code, body = await _get(app, "/_unexpected")

    assert status_code == 500
    assert body["error"]["code"] == "internal_error"
    assert "hunter2" not in str(body)


async def test_cors_headers_are_applied(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
